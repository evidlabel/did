"""Anonymizer class for entity detection and anonymization."""

import re
import yaml
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_analyzer.nlp_engine import SpacyNlpEngine
from .models import Config, Entity
from ..utils import find_name_variants, find_number_variants


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
            "patterns_found": 0,
            "patterns_replaced": 0,
            "cpr_found": 0,
            "cpr_replaced": 0,
        }
        self.entities: Config = Config()
        self.language = language

        number_pattern = Pattern(
            name="NUMBER_PATTERN", regex=r"\b\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\b", score=0.8
        )
        cpr_pattern = Pattern(name="CPR_NUMBER", regex=r"\b\d{6}-\d{4}\b", score=0.95)
        short_number_pattern = Pattern(
            name="SHORT_NUMBER", regex=r"\b\d{7,10}\b", score=0.5
        )
        high_digit_pattern = Pattern(
            name="HIGH_DIGIT",
            regex=r"\b(?=(?:.*?\d){6})[\w-]+\b",
            score=0.6
        )
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(supported_entity="NUMBER_PATTERN", patterns=[number_pattern])
        )
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(supported_entity="CPR_NUMBER", patterns=[cpr_pattern])
        )
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(supported_entity="PHONE_NUMBER", patterns=[short_number_pattern])
        )
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(supported_entity="HIGH_DIGIT", patterns=[high_digit_pattern])
        )

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
        pattern_matches = []
        date_matches = []
        high_matches = []
        all_cpr = []
        for text in texts:
            results = self.analyzer.analyze(
                text=text,
                entities=[
                    "PERSON",
                    "EMAIL_ADDRESS",
                    "LOCATION",
                    "PHONE_NUMBER",
                    "NUMBER_PATTERN",
                    "CPR_NUMBER",
                    "DATE_TIME",
                    "HIGH_DIGIT",
                ],
                language=self.language,
            )
            for result in results:
                entity_text = text[result.start : result.end]
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
                    result.entity_type in ["PHONE_NUMBER", "NUMBER_PATTERN", "DATE_TIME", "HIGH_DIGIT"]
                    and entity_text not in all_numbers
                    and entity_text not in all_cpr
                ):
                    all_numbers.append(entity_text)
                    self.counts["numbers_found"] += 1
                    if result.entity_type == "NUMBER_PATTERN":
                        pattern_matches.append(entity_text)
                        self.counts["patterns_found"] += 1
                    if result.entity_type == "DATE_TIME":
                        date_matches.append(entity_text)
                        self.counts["patterns_found"] += 1
                    if result.entity_type == "HIGH_DIGIT":
                        high_matches.append(entity_text)
                        self.counts["patterns_found"] += 1
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
            entry_dict = {"id": f"<PHONE_NUMBER_{number_count}>", "variants": variants}
            has_pattern = any(v in pattern_matches for v in variants)
            has_date = any(v in date_matches for v in variants)
            has_high = any(v in high_matches for v in variants)
            if has_pattern:
                entry_dict["pattern"] = r"\b\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\b"
            elif has_date:
                entry_dict["pattern"] = r"\b\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2})?\b"
            elif has_high:
                entry_dict["pattern"] = r"\b(?=(?:.*?\d){6})[\w-]+\b"
            self.entities.numbers.append(Entity(**entry_dict))
            number_count += 1
        for cpr in all_cpr:
            self.entities.cpr.append(
                Entity(id=f"<CPR_NUMBER_{cpr_count}>", variants=[cpr])
            )
            cpr_count += 1

    def generate_yaml(self) -> str:
        """Generate YAML configuration from detected entities."""
        return yaml.dump(self.entities.model_dump(exclude_none=True), sort_keys=False)

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
                    pattern = escaped if cat == "addresses" else r"\b" + escaped + r"\b"
                    count = len(re.findall(pattern, text))
                    self.counts[found_key] += count
                    self.counts[replaced_key] += count
                    if cat == "numbers" and entity.pattern:
                        self.counts["patterns_found"] += count
                        self.counts["patterns_replaced"] += count
                    text = re.sub(pattern, entity.id, text)
        return text, self.counts
