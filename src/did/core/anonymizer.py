"""Anonymizer class for entity detection and anonymization."""

import re
import yaml
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_analyzer.nlp_engine import SpacyNlpEngine
from .models import Config, Entity
from ..utils import find_name_variants, find_number_variants
from .number_detector import HighDigitDensityRecognizer
from .general_number_detector import GeneralNumberRecognizer


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

        nlp_engine = SpacyNlpEngine(models=[{"lang_code": language, "model_name": spacy_model}])
        self.analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=[language])

        self.counts = {
            "names_found": 0,
            "names_replaced": 0,
            "emails_found": 0,
            "emails_replaced": 0,
            "addresses_found": 0,
            "addresses_replaced": 0,
            "numbers_found": 0,
            "numbers_replaced": 0,
            "cpr_found": 0,
            "cpr_replaced": 0,
        }
        self.entities: Config = Config()
        self.language = language

        # Add custom recognizers
        cpr_pattern = Pattern(name="CPR_NUMBER", regex=r"\b\d{6}-\d{4}\b", score=0.95)
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(supported_entity="CPR_NUMBER", patterns=[cpr_pattern])
        )
        self.analyzer.registry.add_recognizer(HighDigitDensityRecognizer(min_digits=3, window_size=10, density_threshold=0.3))
        self.analyzer.registry.add_recognizer(GeneralNumberRecognizer())

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
            o_end = positions[d_end - 1] + 1 if d_end > 0 and d_end <= len(positions) else len(text)
            return o_start, o_end

        return detection_text, map_to_original

    def detect_entities(self, texts: list):
        """Detect entities in multiple texts using Presidio."""
        name_count = 1
        email_count = 1
        address_count = 1
        number_count = 1
        cpr_count = 1
        all_names = []
        all_emails = []
        all_addresses = []
        all_numbers = []
        all_cpr = []
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
                entity_text = text[o_start:o_end]
                if result.entity_type == "PERSON" and entity_text not in all_names:
                    all_names.append(entity_text)
                    self.counts["names_found"] += 1
                if result.entity_type == "EMAIL_ADDRESS" and entity_text not in all_emails:
                    all_emails.append(entity_text)
                    self.counts["emails_found"] += 1
                if result.entity_type == "LOCATION" and entity_text not in all_addresses:
                    all_addresses.append(entity_text)
                    self.counts["addresses_found"] += 1
                if result.entity_type == "CPR_NUMBER" and entity_text not in all_cpr:
                    all_cpr.append(entity_text)
                    self.counts["cpr_found"] += 1
                if (
                    result.entity_type in ["NUMBER", "PHONE_NUMBER", "DATE_TIME"]
                    and entity_text not in all_numbers
                    and entity_text not in all_cpr
                ):
                    all_numbers.append(entity_text)
                    self.counts["numbers_found"] += 1
        grouped_names = find_name_variants(all_names)
        for variants in grouped_names:
            self.entities.names.append(
                Entity(id=f"<PERSON_{name_count}>", variants=variants)
            )
            name_count += 1
        for email in all_emails:
            self.entities.emails.append(
                Entity(id=f"<EMAIL_ADDRESS_{email_count}>", variants=[email])
            )
            email_count += 1
        for address in all_addresses:
            self.entities.addresses.append(
                Entity(id=f"<ADDRESS_{address_count}>", variants=[address])
            )
            address_count += 1
        grouped_numbers = find_number_variants(all_numbers)
        for variants in grouped_numbers:
            self.entities.numbers.append(
                Entity(id=f"<NUMBER_{number_count}>", variants=variants)
            )
            number_count += 1
        for cpr in all_cpr:
            self.entities.cpr.append(
                Entity(id=f"<CPR_NUMBER_{cpr_count}>", variants=[cpr])
            )
            cpr_count += 1

    def generate_yaml(self) -> str:
        """Generate YAML configuration from detected entities."""
        data = self.entities.model_dump(exclude_none=True)
        return yaml.safe_dump(data, sort_keys=False)

    def load_replacements(self, config: dict):
        """Load replacements from YAML config using Pydantic validation."""
        self.entities = Config.model_validate(config)

    def anonymize(self, text: str) -> tuple:
        """Anonymize text by replacing known variants from config with their IDs."""
        self.counts = {k: 0 for k in self.counts}
        category_mapping = {
            "names": "names_replaced",
            "emails": "emails_replaced",
            "addresses": "addresses_replaced",
            "numbers": "numbers_replaced",
            "cpr": "cpr_replaced",
        }
        for cat, replaced_key in category_mapping.items():
            found_key = replaced_key.replace("_replaced", "_found")
            entities = getattr(self.entities, cat)
            for entity in entities:
                sorted_variants = sorted(entity.variants, key=len, reverse=True)
                for variant in sorted_variants:
                    escaped = re.escape(variant)
                    if cat in ["names", "numbers", "cpr"] and "\n" in variant:
                        pattern = escaped
                    elif cat == "addresses":
                        pattern = escaped
                    else:
                        pattern = r"\b" + escaped + r"\b"
                    count = len(re.findall(pattern, text))
                    self.counts[found_key] += count
                    self.counts[replaced_key] += count
                    replacement = f'"{entity.id}"' if cat in ["numbers", "cpr"] else entity.id  # Surround with quotes for numbers and CPR
                    text = re.sub(pattern, replacement, text)
        return text, self.counts
