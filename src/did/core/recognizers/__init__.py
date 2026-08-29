"""Custom Presidio recognizers — built by :mod:`factory`."""

from .factory import (
    RECOGNIZER_SPECS,
    build_recognizer,
    build_recognizers,
    get_code_number_recognizer,
    get_custom_recognizers,
    get_date_number_recognizer,
    get_general_number_recognizer,
    get_id_number_recognizer,
    get_location_recognizer,
    get_phone_number_recognizer,
    get_url_recognizer,
)

__all__ = [
    "RECOGNIZER_SPECS",
    "build_recognizer",
    "build_recognizers",
    "get_code_number_recognizer",
    "get_custom_recognizers",
    "get_date_number_recognizer",
    "get_general_number_recognizer",
    "get_id_number_recognizer",
    "get_location_recognizer",
    "get_phone_number_recognizer",
    "get_url_recognizer",
]
