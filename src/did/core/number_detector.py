"""Custom number recognizer using multiple strategies."""

import re

from presidio_analyzer import EntityRecognizer, Pattern, RecognizerResult


class HighDigitDensityRecognizer(EntityRecognizer):
    """Recognizer for number-like sequences using density and regex patterns."""

    def __init__(
        self, min_digits: int = 2, window_size: int = 5, density_threshold: float = 0.4
    ):
        super().__init__(supported_entities=["NUMBER"])
        self.min_digits = min_digits
        self.window_size = window_size
        self.density_threshold = density_threshold
        self.patterns = [
            Pattern(
                name="general_number",
                regex=r"[+(\d][\d\.\-,/()+ ]*(?:[.,+ ][a-zA-Z]{1,3})?",
                score=0.7,
            ),
            Pattern(
                name="date_number",
                regex=r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b",
                score=0.7,
            ),
            Pattern(
                name="id_code", regex=r"\b\d{3,}[-\d]{3,}\s*\(\d{3,}\)\b", score=0.8
            ),
            Pattern(
                name="year_based_id",
                regex=r"\b\d{4}-\d{5}\b",
                score=0.8,
            ),
            Pattern(
                name="parenthesized_code",
                regex=r"\(\d{6}\)",
                score=0.8,
            ),
            Pattern(
                name="four_digit_code",
                regex=r"\b\d{4}\b",
                score=0.6,
            ),
            Pattern(
                name="channel_identifier",
                regex=r"\b\d{1,2},\d{1,2}\.[a-zA-Z]{2,3}\b",
                score=0.7,
            ),
            Pattern(
                name="dotted_triplet",
                regex=r"\d{2}\.\d{2}\.\d{2}",
                score=0.7,
            ),
        ]

    def load(self) -> None:
        pass

    def analyze(
        self, text: str, entities: list[str], nlp_artifacts=None
    ) -> list[RecognizerResult]:
        results = []
        # Density-based analysis
        results.extend(self._analyze_density(text))
        # Regex-based analysis
        for pattern in self.patterns:
            for match in re.finditer(pattern.regex, text):
                matched_text = match.group(0)
                digit_count = sum(1 for c in matched_text if c.isdigit())
                if digit_count >= self.min_digits:
                    add_it = True
                    if " " in matched_text:
                        parts = [p for p in matched_text.split() if p]
                        if any(len(p) >= 5 for p in parts):
                            add_it = False
                            current_pos = match.start()
                            for part in parts:
                                p_start = text.find(part, current_pos, match.end())
                                if p_start == -1:
                                    break
                                p_end = p_start + len(part)
                                part_digit_count = sum(1 for c in part if c.isdigit())
                                if part_digit_count >= self.min_digits:
                                    results.append(
                                        RecognizerResult(
                                            entity_type="NUMBER",
                                            start=p_start,
                                            end=p_end,
                                            score=pattern.score,
                                        )
                                    )
                                current_pos = p_end + 1
                    if add_it:
                        results.append(
                            RecognizerResult(
                                entity_type="NUMBER",
                                start=match.start(),
                                end=match.end(),
                                score=pattern.score,
                            )
                        )
        # Merge overlapping results
        return self._merge_results(results)

    def _analyze_density(self, text: str) -> list[RecognizerResult]:
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
            merged = [intervals[0]]
            for current in intervals[1:]:
                prev = merged[-1]
                if prev[1] >= current[0]:
                    merged[-1] = (prev[0], max(prev[1], current[1]))
                else:
                    merged.append(current)
            density_results = []
            for start, end in merged:
                # Trim to actual number-like substring
                orig_start = start
                orig_end = end
                while start < end and not text[start].isdigit():
                    start += 1
                while end > start and not text[end - 1].isdigit():
                    end -= 1
                if end - start < self.min_digits:
                    continue
                substring = text[start:end]
                if " " in substring:
                    parts = [p for p in substring.split() if p]
                    if any(len(p) >= 5 for p in parts):
                        current_pos = start
                        for part in parts:
                            p_start = text.find(part, current_pos, orig_end)
                            if p_start == -1:
                                break
                            p_end = p_start + len(part)
                            part_digit_count = sum(1 for c in part if c.isdigit())
                            if part_digit_count >= self.min_digits:
                                density_results.append(
                                    RecognizerResult(
                                        entity_type="NUMBER",
                                        start=p_start,
                                        end=p_end,
                                        score=0.7,
                                    )
                                )
                            current_pos = p_end + 1
                        continue
                density_results.append(
                    RecognizerResult(
                        entity_type="NUMBER", start=start, end=end, score=0.7
                    )
                )
            return density_results
        return []

    def _merge_results(self, results: list[RecognizerResult]) -> list[RecognizerResult]:
        if not results:
            return []
        results.sort(key=lambda r: r.start)
        merged = [results[0]]
        for current in results[1:]:
            prev = merged[-1]
            if prev.end >= current.start:
                new_end = max(prev.end, current.end)
                new_score = max(prev.score, current.score)
                merged[-1] = RecognizerResult(
                    entity_type="NUMBER", start=prev.start, end=new_end, score=new_score
                )
            else:
                merged.append(current)
        return merged
