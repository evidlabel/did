"""Anonymizer class for entity detection and anonymization."""
import yaml
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_anonymizer import AnonymizerEngine, OperatorConfig
from ..operators import InstanceCounterAnonymizer
from ..utils import find_name_variants, find_number_variants

class Anonymizer:
    def __init__(self):
        """Initialize Presidio analyzer and entity counters."""
        self.analyzer = AnalyzerEngine(supported_languages=["en"])
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
        }
        self.entities = {
            "names": [],
            "emails": [],
            "addresses": [],
            "numbers": [],
        }
        # Add custom patterns
        address_pattern = Pattern(
            name="US_ADDRESS",
            regex=r"\d{1,5}\s[A-Za-z]+(?:\s[A-Za-z]+)*,\s*[A-Za-z]+,\s*[A-Z]{2}\b",
            score=0.85,
        )
        number_pattern = Pattern(
            name="NUMBER_PATTERN", regex=r"\b\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\b", score=0.8
        )
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(supported_entity="ADDRESS", patterns=[address_pattern])
        )
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(supported_entity="NUMBER_PATTERN", patterns=[number_pattern])
        )

    def detect_entities(self, texts: list):
        """Detect entities in multiple texts using Presidio."""
        name_count = 1
        email_count = 1
        address_count = 1
        number_count = 1

        all_names = []
        all_emails = []
        all_addresses = []
        all_numbers = []
        pattern_matches = []

        for text in texts:
            results = self.analyzer.analyze(
                text=text,
                entities=["PERSON", "EMAIL_ADDRESS", "ADDRESS", "PHONE_NUMBER", "NUMBER_PATTERN"],
                language="en",
            )
            for result in results:
                entity_text = text[result.start:result.end]
                if result.entity_type == "PERSON" and entity_text not in all_names:
                    all_names.append(entity_text)
                    self.counts["names_found"] += 1
                elif result.entity_type == "EMAIL_ADDRESS" and entity_text not in all_emails:
                    all_emails.append(entity_text)
                    self.counts["emails_found"] += 1
                elif result.entity_type == "ADDRESS" and entity_text not in all_addresses:
                    all_addresses.append(entity_text)
                    self.counts["addresses_found"] += 1
                elif result.entity_type in ["PHONE_NUMBER", "NUMBER_PATTERN"] and entity_text not in all_numbers:
                    all_numbers.append(entity_text)
                    self.counts["numbers_found"] += 1
                    if result.entity_type == "NUMBER_PATTERN":
                        pattern_matches.append(entity_text)
                        self.counts["patterns_found"] += 1

        # Group names
        grouped_names = find_name_variants(all_names)
        for variants in grouped_names:
            self.entities["names"].append({"id": f"<PERSON_{name_count}>", "variants": variants})
            name_count += 1

        # Group emails
        for email in all_emails:
            self.entities["emails"].append({"id": f"<EMAIL_{email_count}>", "variants": [email]})
            email_count += 1

        # Group addresses
        for address in all_addresses:
            self.entities["addresses"].append({"id": f"<ADDRESS_{address_count}>", "variants": [address]})
            address_count += 1

        # Group numbers
        grouped_numbers = find_number_variants(all_numbers)
        for variants in grouped_numbers:
            entry = {"id": f"<NUMBER_{number_count}>", "variants": variants}
            if any(v in pattern_matches for v in variants):
                entry["pattern"] = r"\b\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\b"
            self.entities["numbers"].append(entry)
            number_count += 1

    def generate_yaml(self) -> str:
        """Generate YAML configuration from detected entities."""
        config = {
            "names": self.entities["names"],
            "emails": self.entities["emails"],
            "addresses": self.entities["addresses"],
            "numbers": self.entities["numbers"],
        }
        return yaml.dump(config, sort_keys=False)

    def load_replacements(self, config: dict):
        """Load replacements from YAML config and update entity_mapping."""
        self.entities["names"] = config.get("names", [])
        self.entities["emails"] = config.get("emails", [])
        self.entities["addresses"] = config.get("addresses", [])
        self.entities["numbers"] = config.get("numbers", [])

        for entry in self.entities["names"]:
            self.entity_mapping.setdefault("PERSON", {})
            for variant in entry["variants"]:
                self.entity_mapping["PERSON"][variant] = entry["id"]
        for entry in self.entities["emails"]:
            self.entity_mapping.setdefault("EMAIL_ADDRESS", {})
            for variant in entry["variants"]:
                self.entity_mapping["EMAIL_ADDRESS"][variant] = entry["id"]
        for entry in self.entities["addresses"]:
            self.entity_mapping.setdefault("ADDRESS", {})
            for variant in entry["variants"]:
                self.entity_mapping["ADDRESS"][variant] = entry["id"]
        for entry in self.entities["numbers"]:
            self.entity_mapping.setdefault("PHONE_NUMBER", {})
            self.entity_mapping.setdefault("NUMBER_PATTERN", {})
            for variant in entry["variants"]:
                self.entity_mapping["PHONE_NUMBER"][variant] = entry["id"]
                if entry.get("pattern"):
                    self.entity_mapping["NUMBER_PATTERN"][variant] = entry["id"]

    def anonymize(self, text: str) -> tuple:
        """Pseudonymize a single text using Presidio."""
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
        }

        results = self.analyzer.analyze(
            text=text,
            entities=["PERSON", "EMAIL_ADDRESS", "ADDRESS", "PHONE_NUMBER", "NUMBER_PATTERN"],
            language="en",
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

        anonymized_result = self.anonymizer.anonymize(
            text,
            results,
            {"DEFAULT": OperatorConfig("entity_counter", {"entity_mapping": self.entity_mapping})},
        )

        for entry in self.entities["names"]:
            self.counts["names_replaced"] += anonymized_result.text.count(entry["id"])
        for entry in self.entities["emails"]:
            self.counts["emails_replaced"] += anonymized_result.text.count(entry["id"])
        for entry in self.entities["addresses"]:
            self.counts["addresses_replaced"] += anonymized_result.text.count(entry["id"])
        for entry in self.entities["numbers"]:
            self.counts["numbers_replaced"] += anonymized_result.text.count(entry["id"])
            if entry.get("pattern"):
                self.counts["patterns_replaced"] += anonymized_result.text.count(entry["id"])

        return anonymized_result.text, self.counts
