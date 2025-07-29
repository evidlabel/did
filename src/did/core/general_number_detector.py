"""General number recognizer for sequences with at least 2 digits."""

import re

from presidio_analyzer import EntityRecognizer, Pattern, RecognizerResult


class GeneralNumberRecognizer(EntityRecognizer):
    """Recognizer for number-like sequences with at least 2 digits."""

    def __init__(self):
        super().__init__(supported_entities=["NUMBER"])
        self.pattern = Pattern(
            name="general_number",
            regex=r"[+(\d][\d\.\-,/()+ ]*(?:[.,+ ][a-zA-Z]{1,3})?",
            score=0.7
        )

    def load(self) -> None:
        pass

    def analyze(self, text: str, entities: list[str], nlp_artifacts=None) -> list[RecognizerResult]:
        results = []
        for match in re.finditer(self.pattern.regex, text):
            matched_text = match.group(0)
            digit_count = sum(1 for c in matched_text if c.isdigit())
            if digit_count >= 2:
                results.append(
                    RecognizerResult(
                        entity_type="NUMBER",
                        start=match.start(),
                        end=match.end(),
                        score=0.7
                    )
                )
        return results
