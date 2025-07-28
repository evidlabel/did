"""Custom number recognizer for high digit density."""

from presidio_analyzer import EntityRecognizer, RecognizerResult


class HighDigitDensityRecognizer(EntityRecognizer):
    """Recognizer for substrings with high digit density."""

    def __init__(self, min_digits: int = 4, window_size: int = 12, density_threshold: float = 0.4):
        super().__init__(supported_entities=["NUMBER"])
        self.min_digits = min_digits
        self.window_size = window_size
        self.density_threshold = density_threshold

    def load(self) -> None:
        pass

    def analyze(self, text: str, entities: list[str], nlp_artifacts=None) -> list[RecognizerResult]:
        results = []
        intervals = []
        for i in range(len(text) - self.window_size + 1):
            window = text[i : i + self.window_size]
            digit_count = sum(1 for c in window if c.isdigit())
            density = digit_count / len(window)
            if digit_count >= self.min_digits and density >= self.density_threshold:
                intervals.append((i, i + self.window_size))

        # Merge overlapping intervals
        if intervals:
            intervals.sort()
            merged = []
            for interval in intervals:
                if not merged or merged[-1][1] < interval[0]:
                    merged.append(interval)
                else:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], interval[1]))
            for start, end in merged:
                # Trim to actual number-like substring
                while start < end and not text[start].isdigit():
                    start += 1
                while end > start and not text[end - 1].isdigit():
                    end -= 1
                if end - start >= self.min_digits:
                    results.append(
                        RecognizerResult(
                            entity_type="NUMBER", start=start, end=end, score=0.7
                        )
                    )
        return results
