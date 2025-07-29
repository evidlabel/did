"""Anonymizer class for entity detection and anonymization."""

import re
import io  # Added for StringIO
from ruamel.yaml import YAML  # Explicitly using ruamel.yaml
from ruamel.yaml.scalarstring import DoubleQuotedScalarString  # For quoting strings
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_analyzer.nlp_engine import SpacyNlpEngine
from .models import Config, Entity
from ..utils import find_name_variants, find_number_variants
from .number_detector import HighDigitDensityRecognizer

# from .general_number_detector import GeneralNumberRecognizer
from collections import defaultdict


class Anonymizer:
    """Handles entity detection and anonymization."""

    def __init__(self, language="en"):
        # Configure spaCy model based on language
        if language == "en":
            spacy_model = "en_core_web_lg"
        elif language == "da":
            spacy_model = "da_core_news_md"
        else:
            raise ValueError(f"Unsupported language: {language}")

        nlp_engine = SpacyNlpEngine(
            models=[{"lang_code": language, "model_name": spacy_model}]
        )
        self.analyzer = AnalyzerEngine(
            nlp_engine=nlp_engine, supported_languages=[language]
        )

        print("Adding custom recognizers...")
        # Add custom recognizers
        cpr_pattern = Pattern(name="CPR_NUMBER", regex=r"\b\d{6}-\d{4}\b", score=0.95)
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(supported_entity="CPR_NUMBER", patterns=[cpr_pattern])
        )

        self.analyzer.registry.add_recognizer(HighDigitDensityRecognizer())
        # self.analyzer.registry.add_recognizer(GeneralNumberRecognizer())

        self.counts = {
            "person_found": 0,
            "person_replaced": 0,
            "email_address_found": 0,
            "email_address_replaced": 0,
            "location_found": 0,
            "location_replaced": 0,
            "number_found": 0,
            "number_replaced": 0,
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
            "NUMBER": "number",
            "PHONE_NUMBER": "phone",
            "DATE_TIME": "date_time",
            "CPR_NUMBER": "cpr_number",
        }
        all_entities = defaultdict(list)
        for text in texts:
            detection_text, map_to_original = self.preprocess_text(text)
            results = self.analyzer.analyze(
                text=detection_text,
                entities=[
                    "PERSON",
                    "EMAIL_ADDRESS",
                    "LOCATION",
                    "NUMBER",
                    "PHONE_NUMBER",
                    "CPR_NUMBER",
                    "DATE_TIME",
                ],
                language=self.language,
            )
            for result in results:
                o_start, o_end = map_to_original(result.start, result.end)
                try:
                    entity_text = text[
                        o_start:o_end
                    ]  # Added try-except for error handling
                except IndexError:
                    print(
                        f"String index out of range error: o_start={o_start}, o_end={o_end}, len(text)={len(text)}"
                    )  # Log the error
                    entity_text = ""  # Set to empty string to continue
                ent_type = result.entity_type
                if ent_type in type_mapping:
                    mapped = type_mapping[ent_type]
                    if entity_text and entity_text not in all_entities[mapped]:
                        all_entities[mapped].append(entity_text)
                        self.counts[f"{mapped}_found"] += 1

        # Process groupings
        for cat in ["person", "email_address", "location", "number", "cpr_number"]:
            items = all_entities.get(cat, [])
            if cat == "person":
                grouped = find_name_variants(items)
            elif cat == "number":
                grouped = find_number_variants(items)
            else:
                grouped = [[item] for item in items if item]
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
            "number": "number_replaced",
            "date_time": "date_time_replaced",
            "phone": "phone_replaced",
            "cpr_number": "cpr_number_replaced",
        }
        for cat, replaced_key in category_mapping.items():
            found_key = replaced_key.replace("_replaced", "_found")
            entities = getattr(self.entities, cat)
            for entity in entities:
                sorted_variants = sorted(entity.variants, key=len, reverse=True)
                for variant in sorted_variants:
                    escaped = re.escape(variant)
                    if cat in ["person", "number", "cpr_number"] and "\n" in variant:
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
                        if cat in ["number", "cpr_number"]
                        else entity.id
                    )
                    # Surround with quotes for numbers and CPR
                    text = re.sub(pattern, replacement, text)
        return text, self.counts
