from typing import Dict
from presidio_anonymizer.operators import Operator, OperatorType


class InstanceCounterAnonymizer(Operator):
    """Anonymizer that replaces entity values with unique identifiers per entity type."""

    REPLACING_FORMAT = "<{entity_type}_{index}>"

    def operate(self, text: str, params: Dict = None) -> str:
        entity_type: str = params["entity_type"]
        entity_mapping: Dict[Dict, str] = params["entity_mapping"]

        entity_mapping_for_type = entity_mapping.setdefault(entity_type, {})
        if text in entity_mapping_for_type:
            return entity_mapping_for_type[text]

        new_text = self.REPLACING_FORMAT.format(
            entity_type=entity_type, index=len(entity_mapping_for_type) + 1
        )
        entity_mapping_for_type[text] = new_text
        return new_text

    def validate(self, params: Dict = None) -> None:
        if "entity_mapping" not in params:
            raise ValueError("`entity_mapping` is required.")
        if "entity_type" not in params:
            raise ValueError("`entity_type` param is required.")

    def operator_name(self) -> str:
        return "entity_counter"

    def operator_type(self) -> OperatorType:
        return OperatorType.Anonymize
