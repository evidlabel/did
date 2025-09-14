"""Anonymizer class for entity detection and anonymization."""

import re
import io  # Added for StringIO
from ruamel.yaml import YAML  # Explicitly using ruamel.yaml
from ruamel.yaml.scalarstring import DoubleQuotedScalarString  # For quoting strings
from presidio_analyzer import (
    AnalyzerEngine,
    PatternRecognizer,
    Pattern,
    RecognizerRegistry,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import EmailRecognizer, PhoneRecognizer
from .models import Config, Entity
from ..utils import find_name_variants, find_number_variants
from collections import defaultdict


def get_custom_recognizers(language):
    """Return a list of custom PatternRecognizers for different entity types."""
    recognizers = []

    # General Number
    general_patterns = [
        Pattern(
            name="general_number",
            regex=r"[+(\d][\d\.\-,/()+ ]*(?:[.,+ ][a-zA-Z]{1,3})?",
            score=0.7,
        ),
        Pattern(name="DIGIT_SEQUENCE", regex=r"\b\d{4,6}\b", score=0.8),
        Pattern(name="four_digit_code", regex=r"\b\d{4}\b", score=0.6),
    ]
    recognizers.append(
        PatternRecognizer(
            supported_entity="GENERAL_NUMBER",
            patterns=general_patterns,
            context=["account", "phone", "code", "number", "id", "tel", "mobil"],
            denial_context=[
                "st",
                "street",
                "ave",
                "blvd",
                "rd",
                "vej",
                "gade",
                "adresse",
            ],
            supported_language=language,
        )
    )

    # Date Number
    date_patterns = [
        Pattern(
            name="date_number", regex=r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", score=0.7
        ),
        Pattern(name="dotted_triplet", regex=r"\d{2}\.\d{2}\.\d{2}", score=0.7),
    ]
    recognizers.append(
        PatternRecognizer(
            supported_entity="DATE_NUMBER",
            patterns=date_patterns,
            supported_language=language,
        )
    )

    # ID Number
    id_patterns = [
        Pattern(name="id_code", regex=r"\b\d{3,}[-\d]{3,}\s*\(\d{3,}\)\b", score=0.8),
        Pattern(name="year_based_id", regex=r"\b\d{4}-\d{5}\b", score=0.8),
    ]
    recognizers.append(
        PatternRecognizer(
            supported_entity="ID_NUMBER",
            patterns=id_patterns,
            supported_language=language,
        )
    )

    # Code Number
    code_patterns = [
        Pattern(name="parenthesized_code", regex=r"\(\d{6}\)", score=0.8),
        Pattern(
            name="channel_identifier",
            regex=r"\b\d{1,2},\d{1,2}\.[a-zA-Z]{2,3}\b",
            score=0.7,
        ),
    ]
    recognizers.append(
        PatternRecognizer(
            supported_entity="CODE_NUMBER",
            patterns=code_patterns,
            supported_language=language,
        )
    )

    # CPR Number (Danish SSN)
    cpr_patterns = [Pattern(name="cpr_number", regex=r"\b\d{6}-\d{4}\b", score=0.6)]
    recognizers.append(
        PatternRecognizer(
            supported_entity="CPR_NUMBER",
            patterns=cpr_patterns,
            context=["cpr", "personnummer"],
            supported_language=language,
        )
    )

    return recognizers


def filter_non_overlapping(base_results, extra_results):
    """Return extra_results that do not overlap with base_results."""
    non_overlapping = []
    for er in extra_results:
        overlap = False
        for br in base_results:
            if not (er.end <= br.start or er.start >= br.end):
                overlap = True
                break
        if not overlap:
            non_overlapping.append(er)
    return non_overlapping


class Anonymizer:
    """Handles entity detection and anonymization."""

    def __init__(self, language="en"):
        # Configure spaCy model based on language
        conf = {
            "nlp_engine_name": "spacy",
            "models": [
                {"lang_code": "da", "model_name": "da_core_news_md"},
                {"lang_code": "en", "model_name": "en_core_web_md"},
            ],
            "ner_model_configuration": {
                "model_to_presidio_entity_mapping": {
                    "PER": "PERSON",
                    "LOC": "LOCATION",
                    "GPE": "LOCATION",
                    "ORG": "ORGANIZATION",
                    "MISC": "NRP",
                },
                "labels_to_ignore": ["O"],
            },
        }

        nlp_engine = NlpEngineProvider(nlp_configuration=conf).create_engine()
        registry = RecognizerRegistry(supported_languages=[language, "en"])
        registry.load_predefined_recognizers(languages=[language, "en"])
        registry.add_recognizer(EmailRecognizer(supported_language=language))
        registry.add_recognizer(PhoneRecognizer(supported_language=language))
        for custom_recognizer in get_custom_recognizers(language):
            registry.add_recognizer(custom_recognizer)

        self.analyzer = AnalyzerEngine(
            registry=registry,
            nlp_engine=nlp_engine,
            supported_languages=[language, "en"],
        )

        self.counts = {
            "person_found": 0,
            "person_replaced": 0,
            "email_address_found": 0,
            "email_address_replaced": 0,
            "location_found": 0,
            "location_replaced": 0,
            "phone_number_found": 0,
            "phone_number_replaced": 0,
            "date_number_found": 0,
            "date_number_replaced": 0,
            "id_number_found": 0,
            "id_number_replaced": 0,
            "code_number_found": 0,
            "code_number_replaced": 0,
            "general_number_found": 0,
            "general_number_replaced": 0,
            "cpr_number_found": 0,
            "cpr_number_replaced": 0,
        }
        self.entities: Config = Config()
        self.language = language

    def preprocess_text(self, text: str):
        """Preprocess text to join hyphenated multi-line words for detection."""
        positions = []
        detection_text = ""
        i = 0
        while i < len(text):
            if (
                i > 0
                and text[i - 1].isalpha()
                and text[i] == "-"
                and i + 1 < len(text)
                and text[i + 1] == "\n"
                and i + 2 < len(text)
                and text[i + 2].isalpha()
            ):
                i += 2  # Skip -\n
                continue
            detection_text += text[i]
            positions.append(i)
            i += 1

        def map_to_original(d_start: int, d_end: int):
            if d_start >= len(positions):
                return len(text), len(text)
            o_start = positions[d_start]
            o_end = (
                positions[d_end - 1] + 1
                if d_end > 0 and d_end <= len(positions)
                else len(text)
            )
            return o_start, o_end

        return detection_text, map_to_original

    def detect_entities(self, texts: list):
        """Detect entities in multiple texts using Presidio."""
        type_mapping = {
            "PERSON": "person",
            "EMAIL_ADDRESS": "email_address",
            "LOCATION": "location",
            "PHONE_NUMBER": "phone_number",
            "DATE_TIME": "date_number",
            "GENERAL_NUMBER": "general_number",
            "DATE_NUMBER": "date_number",
            "ID_NUMBER": "id_number",
            "CODE_NUMBER": "code_number",
            "CPR_NUMBER": "cpr_number",
        }
        all_entities = defaultdict(list)
        for text in texts:
            detection_text, map_to_original = self.preprocess_text(text)
            # Run all recognizers
            results = self.analyzer.analyze(
                text=detection_text,
                language=self.language,
                entities=None,
            )

            # Sort by score descending to prioritize higher confidence matches
            sorted_results = sorted(results, key=lambda r: -r.score)

            # Select non-overlapping results, preferring higher scores, but skip unmapped to not block mapped ones
            selected_results = []
            for result in sorted_results:
                if result.entity_type not in type_mapping:
                    continue  # Skip unmapped entities to avoid blocking
                overlap = False
                for sel in selected_results:
                    if not (result.end <= sel.start or result.start >= sel.end):
                        overlap = True
                        break
                if not overlap:
                    selected_results.append(result)

            # Process selected results
            for result in selected_results:
                o_start, o_end = map_to_original(result.start, result.end)
                try:
                    entity_text = text[o_start:o_end]
                except IndexError:
                    print(
                        f"Index error: o_start={o_start}, o_end={o_end}, len(text)={len(text)}"
                    )
                    entity_text = ""
                ent_type = result.entity_type
                if ent_type in type_mapping:
                    mapped = type_mapping[ent_type]
                    if entity_text and entity_text not in all_entities[mapped]:
                        all_entities[mapped].append(entity_text)
                        self.counts[f"{mapped}_found"] += 1

        # Process groupings
        for cat in [
            "person",
            "email_address",
            "location",
            "phone_number",
            "date_number",
            "id_number",
            "code_number",
            "general_number",
            "cpr_number",
        ]:
            items = all_entities.get(cat, [])
            if cat == "person":
                grouped = find_name_variants(items)
            elif cat == "email_address" or cat == "location":
                grouped = [[item] for item in items if item]
            else:
                threshold = 95 if cat == "date_number" else 80
                grouped = find_number_variants(items, threshold=threshold)
            count = 1
            for variants in grouped:
                ent_type_upper = cat.upper() if cat != "cpr_number" else "CPR_NUMBER"
                if cat == "email_address":
                    ent_type_upper = "EMAIL_ADDRESS"
                getattr(self.entities, cat).append(
                    Entity(id=f"<{ent_type_upper}_{count}>", variants=variants)
                )
                count += 1

    def generate_yaml(self) -> str:
        """Generate YAML configuration from detected entities with all strings quoted."""
        data = self.entities.model_dump(by_alias=True, exclude_none=True)

        # Function to recursively quote all strings
        def quote_strings(obj):
            if isinstance(obj, dict):
                return {k: quote_strings(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [quote_strings(item) for item in obj]
            elif isinstance(obj, str):
                return DoubleQuotedScalarString(obj)  # Wrap strings in double quotes
            else:
                return obj  # Leave other types as is

        quoted_data = quote_strings(data)  # Apply quoting to data

        yaml_instance = YAML()  # Create YAML instance
        stream = io.StringIO()  # Use StringIO for string output
        yaml_instance.dump(quoted_data, stream)  # Dump the quoted data
        return stream.getvalue()  # Return the string

    def load_replacements(self, config: dict):
        """Load replacements from YAML config using Pydantic validation."""
        self.entities = Config.model_validate(config)

    def anonymize(self, text: str) -> tuple:
        """Anonymize text by replacing known variants from config with their IDs."""
        self.counts = {k: 0 for k in self.counts}
        category_mapping = {
            "person": "person_replaced",
            "email_address": "email_address_replaced",
            "location": "location_replaced",
            "phone_number": "phone_number_replaced",
            "date_number": "date_number_replaced",
            "id_number": "id_number_replaced",
            "code_number": "code_number_replaced",
            "general_number": "general_number_replaced",
            "cpr_number": "cpr_number_replaced",
        }
        for cat, replaced_key in category_mapping.items():
            found_key = replaced_key.replace("_replaced", "_found")
            entities = getattr(self.entities, cat)
            for entity in entities:
                sorted_variants = sorted(entity.variants, key=len, reverse=True)
                for variant in sorted_variants:
                    escaped = re.escape(variant)
                    if (
                        cat
                        in [
                            "person",
                            "phone_number",
                            "date_number",
                            "id_number",
                            "code_number",
                            "general_number",
                            "cpr_number",
                        ]
                        and "\n" in variant
                    ):
                        pattern = escaped
                    elif cat == "location":
                        pattern = escaped
                    else:
                        pattern = r"\b" + escaped + r"\b"
                    count = len(re.findall(pattern, text))
                    self.counts[found_key] += count
                    self.counts[replaced_key] += count
                    replacement = (
                        f'"{entity.id}"'
                        if cat
                        in [
                            "phone_number",
                            "date_number",
                            "id_number",
                            "code_number",
                            "general_number",
                            "cpr_number",
                        ]
                        else entity.id
                    )
                    # Surround with quotes for numbers and CPR
                    text = re.sub(pattern, replacement, text)
        return text, self.counts
