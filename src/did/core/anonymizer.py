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
    "thorough": {"da": "da_core_news_lg", "en": "en_core_web_md"},
    "balanced": {"da": "da_core_news_sm", "en": "en_core_web_md"},
}
MODELS_INSTALL_HINT = "uv sync --extra models"


def missing_spacy_models(detection_profile="thorough"):
    """Return spaCy model package names that are not installed for *profile*."""
    if detection_profile not in SPACY_MODELS:
        raise ValueError(f"Unknown detection profile: {detection_profile!r}")
    return [
        name
        for name in SPACY_MODELS[detection_profile].values()
        if not spacy.util.is_package(name)
    ]


def require_spacy_models(detection_profile="thorough"):
    """Raise ValueError listing missing models instead of letting spaCy pip-install.

    Presidio calls ``spacy.cli.download`` when a model is absent. That runs
    ``python -m pip``, which uv venvs do not provide, and spaCy then
    ``sys.exit``s — crashing a GUI QThread rather than surfacing an error.
    """
    missing = missing_spacy_models(detection_profile)
    if missing:
        raise ValueError(
            "Required spaCy model(s) not installed: "
            + ", ".join(missing)
            + f". Install with: {MODELS_INSTALL_HINT}"
        )


class Anonymizer:
    """Handles entity detection and anonymization."""

    def __init__(self, language="en", detection_profile="thorough"):
        if detection_profile not in SPACY_MODELS:
            raise ValueError(f"Unknown detection profile: {detection_profile!r}")
        selected_models = SPACY_MODELS[detection_profile]
        conf = {
            "nlp_engine_name": "spacy",
            "models": [
                {"lang_code": "da", "model_name": selected_models["da"]},
                {"lang_code": "en", "model_name": selected_models["en"]},
            ],
            "ner_model_configuration": {
                "model_to_presidio_entity_mapping": {
                    "PER": "PERSON",
                    "GPE": "LOCATION",
                    "ORG": "ORGANIZATION",
                    "MISC": "NRP",
                },
                "labels_to_ignore": ["O"],
            },
        }

        require_spacy_models(detection_profile)
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
