"""Utility functions for entity processing."""
from rapidfuzz import fuzz

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
        and not any(word.lower() in ["multiline", "phone", "account", "code", "street"] for word in words)
    )

def find_name_variants(names: list, threshold: float = 85) -> list:
    """Group similar names using rapidfuzz."""
    if not names:
        return []
    valid_names = [name for name in names if is_valid_name(name)]
    if not valid_names:
        return []
    grouped_names = []
    processed = set()
    for name in valid_names:
        if name in processed:
            continue
        variants = [name]
        processed.add(name)
        for other_name in valid_names:
            if other_name not in processed:
                score = fuzz.ratio(normalize_name(name), normalize_name(other_name))
                if score > threshold:
                    variants.append(other_name)
                    processed.add(other_name)
        if variants:
            grouped_names.append(variants)
    return grouped_names

def find_number_variants(numbers: list, threshold: float = 80) -> list:
    """Group similar numbers using rapidfuzz."""
    if not numbers:
        return []
    grouped_numbers = []
    processed = set()
    for number in numbers:
        if number in processed:
            continue
        variants = [number]
        processed.add(number)
        for other_number in numbers:
            if other_number not in processed:
                score = fuzz.ratio(normalize_number(number), normalize_number(other_number))
                if score > threshold:
                    variants.append(other_number)
                    processed.add(other_number)
        if variants:
            grouped_numbers.append(variants)
    return grouped_numbers
