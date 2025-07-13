"""Utility functions for entity processing."""

import numpy as np
from rapidfuzz import fuzz
from rapidfuzz.process import cdist


def normalize_name(name: str) -> str:
    """Normalize a name for comparison."""
    return (
        name.lower()
        .replace("å", "aa")
        .replace("æ", "ae")
        .replace("ø", "oe")
        .replace("-", "")
        .replace("\n", " ")
    )


def normalize_number(number: str) -> str:
    """Normalize a number for comparison."""
    return number.replace(" ", "").replace("-", "")


def is_valid_name(name: str) -> bool:
    """Check if a string is a valid name."""
    words = name.strip().split()
    return (
        1 <= len(words) <= 3
        and all(any(c.isalpha() for c in word) for word in words)
        and not any(
            word.lower() in ["multiline", "phone", "account", "code", "street"]
            for word in words
        )
    )


def find_name_variants(names: list, threshold: float = 85) -> list:
    """Group similar names using vectorized rapidfuzz."""
    if not names:
        return []
    valid_names = [name for name in names if is_valid_name(name)]
    if not valid_names:
        return []
    normalized = [normalize_name(name) for name in valid_names]
    scores = cdist(normalized, normalized, scorer=fuzz.ratio)
    grouped_names = []
    visited = np.zeros(len(valid_names), dtype=bool)
    for i in range(len(valid_names)):
        if visited[i]:
            continue
        variants = [valid_names[i]]
        visited[i] = True
        # Collect all directly similar
        similar = np.where(scores[i] > threshold)[0]
        for j in similar:
            if not visited[j]:
                variants.append(valid_names[j])
                visited[j] = True
        if variants:
            grouped_names.append(variants)
    return grouped_names


def find_number_variants(numbers: list, threshold: float = 80) -> list:
    """Group similar numbers using vectorized rapidfuzz."""
    if not numbers:
        return []
    normalized = [normalize_number(num) for num in numbers]
    scores = cdist(normalized, normalized, scorer=fuzz.ratio)
    grouped_numbers = []
    visited = np.zeros(len(numbers), dtype=bool)
    for i in range(len(numbers)):
        if visited[i]:
            continue
        variants = [numbers[i]]
        visited[i] = True
        similar = np.where(scores[i] > threshold)[0]
        for j in similar:
            if not visited[j]:
                variants.append(numbers[j])
                visited[j] = True
        if variants:
            grouped_numbers.append(variants)
    return grouped_numbers
