"""The single declaration site for the entity types DID tokenizes.

Every prefix, placeholder word, and category name in the codebase derives from
``ENTITY_TYPES``. Six modules used to keep parallel lists by hand, and they
drifted: the Typst exporter's list omitted ``organization`` while the replacer's
included it, so ``#(O1V1)`` reached document bodies with no ``#let O1V1`` to
resolve it — the export compiled to an undefined-variable error and the real
value never reached the key set at all.

Add a type here and nowhere else.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class EntityType:
    """One kind of identifier DID knows how to replace with a token."""

    #: Attribute name on :class:`did.core.models.Entities`.
    category: str
    #: Section name in the YAML review config.
    config_key: str
    #: Token prefix, as in the ``P`` of ``#(P1V1)``.
    prefix: str
    #: Written-out form used by the GUI's plain-language preview mode.
    word: str
    #: Digits are faked digit-by-digit so the shape of the value survives.
    number_like: bool = False
    #: False for types that are assigned rather than found by the model.
    detected: bool = True


PERSON = EntityType("person", "PERSON", "P", "PERSON")
ORGANIZATION = EntityType("organization", "ORGANIZATION", "O", "ORGANIZATION")
EMAIL_ADDRESS = EntityType("email_address", "EMAIL_ADDRESS", "E", "EMAIL")
LOCATION = EntityType("location", "LOCATION", "A", "ADDRESS")
PHONE_NUMBER = EntityType(
    "phone_number", "PHONE_NUMBER", "PH", "PHONE", number_like=True
)
DATE_NUMBER = EntityType("date_number", "DATE_NUMBER", "DT", "DATE", number_like=True)
ID_NUMBER = EntityType("id_number", "ID_NUMBER", "ID", "ID", number_like=True)
CODE_NUMBER = EntityType("code_number", "CODE_NUMBER", "CD", "CODE", number_like=True)
GENERAL_NUMBER = EntityType(
    "general_number", "GENERAL_NUMBER", "GN", "NUMBER", number_like=True
)
URL = EntityType("url", "URL", "URL", "URL")

#: Document titles are assigned one per input document, in processing order.
#: They are never detected: a filename is metadata attached to a document, not
#: a span the model can find inside one.
DOCUMENT_TITLE = EntityType(
    "document_title", "DOCUMENT_TITLE", "DOC", "DOCUMENT", detected=False
)

ENTITY_TYPES = (
    PERSON,
    ORGANIZATION,
    EMAIL_ADDRESS,
    LOCATION,
    PHONE_NUMBER,
    DATE_NUMBER,
    ID_NUMBER,
    CODE_NUMBER,
    GENERAL_NUMBER,
    URL,
    DOCUMENT_TITLE,
)

#: Types the detector asks the model for. Everything else covers all of them.
DETECTED_TYPES = tuple(entity for entity in ENTITY_TYPES if entity.detected)

PREFIX_MAP = {entity.category: entity.prefix for entity in ENTITY_TYPES}
CATEGORY_MAPPING = {
    entity.category: f"{entity.category}_replaced" for entity in ENTITY_TYPES
}
PLACEHOLDER_WORDS = {entity.prefix: entity.word for entity in ENTITY_TYPES}
NUMBER_CATEGORIES = {entity.category for entity in ENTITY_TYPES if entity.number_like}
CONFIG_KEY_TO_CATEGORY = {entity.config_key: entity.category for entity in ENTITY_TYPES}
CATEGORY_TO_CONFIG_KEY = {entity.category: entity.config_key for entity in ENTITY_TYPES}


def prefix_pattern() -> str:
    """Alternation of every prefix, longest first so ``PH`` beats ``P``.

    Returned as a non-capturing group: a bare alternation splits whatever it is
    embedded in, so ``#\\({prefix_pattern()}\\d+V\\d+\\)`` would otherwise match
    the literal ``#(URL`` and nothing else.
    """
    ordered = sorted((entity.prefix for entity in ENTITY_TYPES), key=len, reverse=True)
    return "(?:" + "|".join(ordered) + ")"


def token_re() -> re.Pattern:
    """Match ``#(<PREFIX><n>V<m>)``, capturing prefix, entity number, variant."""
    return re.compile(rf"#\(({prefix_pattern()})(\d+)V(\d+)\)")
