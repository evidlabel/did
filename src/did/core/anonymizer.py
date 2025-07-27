"""Anonymizer class for entity detection and anonymization."""

import re
import yaml
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_analyzer.nlp_engine import SpacyNlpEngine
from presidio_anonymizer import AnonymizerEngine, OperatorConfig
from ..operators import InstanceCounterAnonymizer
from ..utils import find_name_variants, find_number_variants
from .models import Config, Entity


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

        self.anonymizer_engine = AnonymizerEngine()
        self.anonymizer_engine.add_anonymizer(InstanceCounterAnonymizer)
        self.entity_mapping = {}
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
        address_pattern = Pattern(
            name="US_ADDRESS",
            regex=r"\d{1,5}\s[A-Za-z]+(?:\s[A-Za-z]+)*,\s*[A-Za-z]+,\s*[A-Z]{2}\b",
            score=0.85,
        )
        number_pattern = Pattern(
            name="NUMBER_PATTERN", regex=r"\b\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\b", score=0.8
        )
        cpr_pattern = Pattern(name="CPR_NUMBER", regex=r"\b\d{6}-\d{4}\b", score=0.95)
        short_number_pattern = Pattern(
            name="SHORT_NUMBER", regex=r"\b\d{7,10}\b", score=0.5
        )
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(supported_entity="ADDRESS", patterns=[address_pattern])
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
        all_cpr = []
        for text in texts:
            results = self.analyzer.analyze(
                text=text,
                entities=[
                    "PERSON",
                    "EMAIL_ADDRESS",
                    "ADDRESS",
                    "PHONE_NUMBER",
                    "NUMBER_PATTERN",
                    "CPR_NUMBER",
                ],
                language=self.language,
            )
            for result in results:
                entity_text = text[result.start : result.end]
                if result.entity_type == "PERSON" and entity_text not in all_names:
                    all_names.append(entity_text)
                    self.counts["names_found"] += 1
                elif result.entity_type == "EMAIL_ADDRESS" and entity_text not in all_emails:
                    all_emails.append(entity_text)
                    self.counts["emails_found"] += 1
                elif result.entity_type == "ADDRESS" and entity_text not in all_addresses:
                    all_addresses.append(entity_text)
                    self.counts["addresses_found"] += 1
                elif (
                    result.entity_type in ["PHONE_NUMBER", "NUMBER_PATTERN"]
                    and entity_text not in all_numbers
                ):
                    all_numbers.append(entity_text)
                    self.counts["numbers_found"] += 1
                    if result.entity_type == "NUMBER_PATTERN":
                        pattern_matches.append(entity_text)
                        self.counts["patterns_found"] += 1
                elif result.entity_type == "CPR_NUMBER" and entity_text not in all_cpr:
                    all_cpr.append(entity_text)
                    self.counts["cpr_found"] += 1
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
            if any(v in pattern_matches for v in variants):
                entry_dict["pattern"] = r"\b\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\b"
            self.entities.numbers.append(Entity(**entry_dict))
            number_count += 1
        for cpr in all_cpr:
            self.entities.cpr.append(
                Entity(id=f"<CPR_NUMBER_{cpr_count}>", variants=[cpr])
            )
            cpr_count += 1

    def generate_yaml(self) -> str:
        """Generate YAML configuration from detected entities."""
        return yaml.dump(self.entities.model_dump(), sort_keys=False)

    def load_replacements(self, config: dict):
        """Load replacements from YAML config using Pydantic validation."""
        self.entities = Config.model_validate(config)
        entity_type_mapping = {
            "names": "PERSON",
            "emails": "EMAIL_ADDRESS",
            "addresses": "ADDRESS",
            "numbers": "PHONE_NUMBER",
            "cpr": "CPR_NUMBER",
        }
        for category, entity_type in entity_type_mapping.items():
            self.entity_mapping[entity_type] = {}
            for entry in getattr(self.entities, category):
                for variant in entry.variants:
                    self.entity_mapping[entity_type][variant] = entry.id

    def anonymize(self, text: str) -> tuple:
        """Anonymize text by detecting entities and applying replacements."""
        results = self.analyzer.analyze(
            text=text,
            entities=[
                "PERSON",
                "EMAIL_ADDRESS",
                "ADDRESS",
                "PHONE_NUMBER",
                "NUMBER_PATTERN",
                "CPR_NUMBER",
            ],
            language=self.language,
        )
        # Reset counts for this anonymization
        self.counts = {k: 0 for k in self.counts}
        for r in results:
            if r.entity_type == "PERSON":
                self.counts["names_found"] += 1
            elif r.entity_type == "EMAIL_ADDRESS":
                self.counts["emails_found"] += 1
            elif r.entity_type == "ADDRESS":
                self.counts["addresses_found"] += 1
            elif r.entity_type == "PHONE_NUMBER":
                self.counts["numbers_found"] += 1
            elif r.entity_type == "NUMBER_PATTERN":
                self.counts["patterns_found"] += 1
                self.counts["numbers_found"] += 1  # Count as number too
            elif r.entity_type == "CPR_NUMBER":
                self.counts["cpr_found"] += 1
        anonymized_result = self.anonymizer_engine.anonymize(
            text,
            results,
            {"DEFAULT": OperatorConfig("entity_counter", {"entity_mapping": self.entity_mapping})},
        )
        # Count replacements
        self.counts["names_replaced"] = len(re.findall(r"<PERSON_\d+>", anonymized_result.text))
        self.counts["emails_replaced"] = len(re.findall(r"<EMAIL_ADDRESS_\d+>", anonymized_result.text))
        self.counts["addresses_replaced"] = len(re.findall(r"<ADDRESS_\d+>", anonymized_result.text))
        self.counts["numbers_replaced"] = len(re.findall(r"<PHONE_NUMBER_\d+>", anonymized_result.text))
        self.counts["patterns_replaced"] = self.counts["numbers_replaced"]  # Approximate, as patterns are subset
        self.counts["cpr_replaced"] = len(re.findall(r"<CPR_NUMBER_\d+>", anonymized_result.text))
        return anonymized_result.text, self.counts
