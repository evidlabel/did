"""Test for Danish faker example."""

import logging

from did.core.anonymizer import Anonymizer

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# @pytest.mark.skip(reason="Danish faker example test is currently disabled.")
def test_anonymize_danish_faker_example():
    """Test anonymization on the provided Danish faker example data."""
    anonymizer = Anonymizer(language="da")
    text = """# Rapport om Projektet

Dette er en rapport skrevet af Dr. Aage Nørgaard og Emma Lauridsen. 
Vi arbejder på Adressen Vangsågade 271
3790 Ringe. 
Kontakt os på telefon 29973033 eller email ingeskov@example.com. 
Projektet startede den 2020-12-12. 
CPR-nummer: 506-03-1346. 
Konto: DK540955044992950649.

Flere detaljer:
- Navn: Svenning Lund
- Adresse: Gladiolus Allé 2
5172 Martofte
- Telefon: 94033638
- Email: albertkjaer@example.org"""
    anonymizer.detect_entities([text])
    logger.info("Detected counts: %s", anonymizer.counts)
    # Check detected counts based on actual output
    # assert anonymizer.counts["person_found"] == 3
    # assert anonymizer.counts["email_address_found"] == 2
    # assert anonymizer.counts["location_found"] == 2
    # assert anonymizer.counts["phone_number_found"] == 0
    # assert anonymizer.counts["date_number_found"] == 0  # Detected as general
    # # assert anonymizer.counts["id_number_found"] == 1
    # assert anonymizer.counts["code_number_found"] == 0
    # assert anonymizer.counts["general_number_found"] == 11
    # Load replacements and anonymize
    anonymizer.load_replacements(
        anonymizer.entities.model_dump(by_alias=True, exclude_none=True)
    )
    result, counts = anonymizer.anonymize(text)
    logger.info("Anonymized result:\n%s", result)

    # Assert the safety contract rather than unstable category numbering.
    sensitive_values = [
        "Aage Nørgaard",
        "Emma Lauridsen",
        "Svenning Lund",
        "ingeskov@example.com",
        "albertkjaer@example.org",
        "Vangsågade 271",
        "Gladiolus Allé 2",
        "2020-12-12",
        "506-03-1346",
        "DK540955044992950649",
        "29973033",
        "94033638",
    ]
    for value in sensitive_values:
        assert value not in result
    assert counts["person_replaced"] >= 3
    assert counts["email_address_replaced"] >= 2
    assert (
        sum(
            counts[key]
            for key in (
                "location_replaced",
                "phone_number_replaced",
                "date_number_replaced",
                "id_number_replaced",
                "general_number_replaced",
            )
        )
        >= 6
    )
