"""Anonymizer class for entity detection and anonymization."""

import yaml
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_anonymizer import AnonymizerEngine, OperatorConfig
from ..operators import InstanceCounterAnonymizer
from ..utils import find_name_variants, find_number_variants


class Anonymizer:
    def __init__(self, language="en"):
        """Initialize Presidio analyzer with specified language."""
        self.analyzer = AnalyzerEngine(supported_languages=[language])
        self.anonymizer = AnonymizerEngine()
        self.anonymizer.add_anonymizer(InstanceCounterAnonymizer)
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
        self.entities = {
            "names": [],
            "emails": [],
            "addresses": [],
            "numbers": [],
            "cpr": [],
        }
        address_pattern = Pattern(
            name="US_ADDRESS",
            regex=r"\d{1,5}\s[A-Za-z]+(?:\s[A-Za-z]+)*,\s*[A-Za-z]+,\s*[A-Z]{2}\b",
            score=0.85,
        )
        number_pattern = Pattern(
            name="NUMBER_PATTERN", regex=r"\b\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\b", score=0.8
        )
        cpr_pattern = Pattern(name="CPR_NUMBER", regex=r"\b\d{6}-\d{4}\b", score=0.95)
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(supported_entity="ADDRESS", patterns=[address_pattern])
        )
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="NUMBER_PATTERN", patterns=[number_pattern]
            )
        )
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(supported_entity="CPR_NUMBER", patterns=[cpr_pattern])
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
                language=self.analyzer.supported_languages[
                    0
                ],  # Use configured language
            )
            for result in results:
                entity_text = text[result.start : result.end]
                if result.entity_type == "PERSON" and entity_text not in all_names:
                    all_names.append(entity_text)
                    self.counts["names_found"] += 1
                elif (
                    result.entity_type == "EMAIL_ADDRESS"
                    and entity_text not in all_emails
                ):
                    all_emails.append(entity_text)
                    self.counts["emails_found"] += 1
                elif (
                    result.entity_type == "ADDRESS" and entity_text not in all_addresses
                ):
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
            self.entities["names"].append(
                {"id": f"<PERSON_{name_count}>", "variants": variants}
            )
            name_count += 1
        for email in all_emails:
            self.entities["emails"].append(
                {"id": f"<EMAIL_{email_count}>", "variants": [email]}
            )
            email_count += 1
        for address in all_addresses:
            self.entities["addresses"].append(
                {"id": f"<ADDRESS_{address_count}>", "variants": [address]}
            )
            address_count += 1
        grouped_numbers = find_number_variants(all_numbers)
        for variants in grouped_numbers:
            entry = {"id": f"<NUMBER_{number_count}>", "variants": variants}
            if any(v in pattern_matches for v in variants):
                entry["pattern"] = r"\b\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\b"
            self.entities["numbers"].append(entry)
            number_count += 1
        for cpr in all_cpr:
            self.entities["cpr"].append({"id": f"<CPR_{cpr_count}>", "variants": [cpr]})
            cpr_count += 1

    def generate_yaml(self) -> str:
        """Generate YAML configuration from detected entities."""
        config = {
            "names": self.entities["names"],
            "emails": self.entities["emails"],
            "addresses": self.entities["addresses"],
            "numbers": self.entities["numbers"],
            "cpr": self.entities["cpr"],
        }
        return yaml.dump(config, sort_keys=False)

    def load_replacements(self, config: dict):
        """Load replacements from YAML config."""
        self.entities = config
        for category in self.entities:
            self.entity_mapping[category] = {}
            for entry in self.entities[category]:
                for variant in entry["variants"]:
                    self.entity_mapping[category][variant] = entry["id"]

    def anonymize(self, text: str) -> tuple:
        """Anonymize text using Presidio."""
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
            language=self.analyzer.supported_languages[0],
        )
        for result in results:
            if result.entity_type == "PERSON":
                self.counts["names_found"] += 1
            elif result.entity_type == "EMAIL_ADDRESS":
                self.counts["emails_found"] += 1
            elif result.entity_type == "ADDRESS":
                self.counts["addresses_found"] += 1
            elif result.entity_type in ["PHONE_NUMBER", "NUMBER_PATTERN"]:
                self.counts["numbers_found"] += 1
                if result.entity_type == "NUMBER_PATTERN":
                    self.counts["patterns_found"] += 1
            elif result.entity_type == "CPR_NUMBER":
                self.counts["cpr_found"] += 1
        anonymized_result = self.anonymizer.anonymize(
            text,
            results,
            {
                "DEFAULT": OperatorConfig(
                    "entity_counter", {"entity_mapping": self.entity_mapping}
                )
            },
        )
        for entry in self.entities.get("names", []):
            self.counts["names_replaced"] += anonymized_result.text.count(entry["id"])
        for entry in self.entities.get("emails", []):
            self.counts["emails_replaced"] += anonymized_result.text.count(entry["id"])
        for entry in self.entities.get("addresses", []):
            self.counts["addresses_replaced"] += anonymized_result.text.count(
                entry["id"]
            )
        for entry in self.entities.get("numbers", []):
            self.counts["numbers_replaced"] += anonymized_result.text.count(entry["id"])
            if entry.get("pattern"):
                self.counts["patterns_replaced"] += anonymized_result.text.count(
                    entry["id"]
                )
        for entry in self.entities.get("cpr", []):
            self.counts["cpr_replaced"] += anonymized_result.text.count(entry["id"])
        return anonymized_result.text, self.counts
