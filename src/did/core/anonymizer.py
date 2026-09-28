"""Main Anonymizer class."""

import spacy
from presidio_analyzer import (
    AnalyzerEngine,
    RecognizerRegistry,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import EmailRecognizer

from . import entity_types
from .config import generate_yaml, load_replacements
from .detection import detect_entities, preprocess_text
from .models import Config
from .recognizers import get_custom_recognizers
from .replacement import anonymize

SPACY_MODELS = {
    "thorough": {
        "da": "da_core_news_lg",
        "en": "en_core_web_md",
        "sv": "sv_core_news_lg",
    },
    "balanced": {
        "da": "da_core_news_sm",
        "en": "en_core_web_md",
        "sv": "sv_core_news_sm",
    },
}
# Swedish spaCy NER uses PRS/LOC/TME. Danish uses PER/GPE. Both feed the same
# Presidio types.
NER_LABEL_MAPPING = {
    "PER": "PERSON",
    "PRS": "PERSON",
    "GPE": "LOCATION",
    "LOC": "LOCATION",
    "ORG": "ORGANIZATION",
    "MISC": "NRP",
    "TME": "DATE_TIME",
}
MODELS_INSTALL_HINT = "did models  (or, in a checkout: make models)"


def models_for(language, detection_profile="thorough"):
    """Model per language code needed to detect *language*.

    English is always included: the analyzer registers it alongside the
    document language. Other languages' models are not needed, so a Danish
    case never requires the Swedish model to be installed.
    """
    if detection_profile not in SPACY_MODELS:
        raise ValueError(f"Unknown detection profile: {detection_profile!r}")
    selected = SPACY_MODELS[detection_profile]
    if language not in selected:
        raise ValueError(
            f"Unsupported language: {language!r}. Choose from {', '.join(selected)}."
        )
    return {code: selected[code] for code in dict.fromkeys([language, "en"])}


def missing_spacy_models(detection_profile="thorough", languages=None):
    """Return model package names not installed for *profile* and *languages*.

    With no *languages*, every language of the profile is checked.
    """
    if detection_profile not in SPACY_MODELS:
        raise ValueError(f"Unknown detection profile: {detection_profile!r}")
    names = {}
    for language in languages or SPACY_MODELS[detection_profile]:
        names.update(models_for(language, detection_profile))
    return [
        name
        for name in dict.fromkeys(names.values())
        if not spacy.util.is_package(name)
    ]


def require_spacy_models(detection_profile="thorough", languages=None):
    """Raise ValueError listing missing models instead of letting spaCy pip-install.

    Presidio calls ``spacy.cli.download`` when a model is absent. That runs
    ``python -m pip``, which uv venvs do not provide, and spaCy then
    ``sys.exit``s — crashing a GUI QThread rather than surfacing an error.
    """
    missing = missing_spacy_models(detection_profile, languages)
    if missing:
        raise ValueError(
            "Required spaCy model(s) not installed: "
            + ", ".join(missing)
            + f". Install with: {MODELS_INSTALL_HINT}"
        )


class Anonymizer:
    """Handles entity detection and anonymization."""

    def __init__(self, language="en", detection_profile="thorough"):
        selected_models = models_for(language, detection_profile)
        conf = {
            "nlp_engine_name": "spacy",
            "models": [
                {"lang_code": code, "model_name": model_name}
                for code, model_name in selected_models.items()
            ],
            "ner_model_configuration": {
                "model_to_presidio_entity_mapping": NER_LABEL_MAPPING,
                "labels_to_ignore": ["O"],
            },
        }

        require_spacy_models(detection_profile, [language])
        try:
            nlp_engine = NlpEngineProvider(nlp_configuration=conf).create_engine()
        except SystemExit as e:
            raise ValueError(
                "NLP engine could not be created for language "
                f"'{language}': spaCy tried to pip-install a missing model. "
                f"Install with: {MODELS_INSTALL_HINT}"
            ) from e
        except Exception as e:
            raise ValueError(
                f"NLP engine could not be created for language '{language}': {e}"
            ) from e

        registry = RecognizerRegistry(supported_languages=[language, "en"])
        registry.load_predefined_recognizers(languages=[language, "en"])
        registry.add_recognizer(EmailRecognizer(supported_language=language))
        for custom_recognizer in get_custom_recognizers(language):
            registry.add_recognizer(custom_recognizer)

        self.analyzer = AnalyzerEngine(
            registry=registry,
            nlp_engine=nlp_engine,
            supported_languages=[language, "en"],
        )

        # One found/replaced pair per registered type. Kept in step with the
        # registry so a new type cannot raise KeyError the first time something
        # is replaced for it.
        self.counts = dict.fromkeys(
            [
                f"{entity.category}_{suffix}"
                for entity in entity_types.ENTITY_TYPES
                for suffix in ("found", "replaced")
            ],
            0,
        )
        self.entities: Config = Config()
        self.language = language
        self.detection_profile = detection_profile
        self.model_map = selected_models

    @classmethod
    def for_regex_only(cls, language="en", detection_profile="thorough"):
        """An Anonymizer with no spaCy engine, for regex-only work.

        The GUI runs detection in a child process (spaCy holds the GIL, which
        would freeze Qt). The parent still needs an object that can load reviewed
        keys and rewrite text; that path is pure regex and needs no model.
        """
        instance = cls.__new__(cls)
        instance.language = language
        instance.detection_profile = detection_profile
        instance.model_map = {}
        instance.entities = Config()
        instance.counts = dict.fromkeys(
            [
                f"{entity.category}_{suffix}"
                for entity in entity_types.ENTITY_TYPES
                for suffix in ("found", "replaced")
            ],
            0,
        )
        return instance

    def detect_entities(self, texts: list):
        """Detect entities in multiple texts using Presidio."""
        detect_entities(self, texts)

    def generate_yaml(self) -> str:
        """Generate YAML configuration from detected entities with all strings quoted."""
        return generate_yaml(self)

    def load_replacements(self, config: dict):
        """Load replacements from YAML config using explicit mapping for robustness."""
        load_replacements(self, config)

    def anonymize(self, text: str) -> tuple:
        """Anonymize text by replacing known variants with Typst-style parameters in a single pass."""
        return anonymize(self, text)

    def preprocess_text(self, text: str):
        """Preprocess text to join hyphenated multi-line words for detection."""
        return preprocess_text(text)
