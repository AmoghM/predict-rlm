"""Fast, no-network smoke tests for the financial packages RLM.

These prove the package imports, the signature exposes the expected fields, and
the service constructs — without Deno, Pyodide, API keys, or any LLM call.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_service_constructs():
    from financial_rlm import FinancialPackageNormalizer

    service = FinancialPackageNormalizer(max_iterations=1, verbose=False, debug=False)
    assert service.max_iterations == 1


def test_signature_has_fields():
    from financial_rlm.agent.signature import ExtractFinancialPackages

    assert set(ExtractFinancialPackages.input_fields) == {"packages"}
    assert set(ExtractFinancialPackages.output_fields) == {"workbook", "result"}


def test_result_schema_fields():
    from financial_rlm import PropertyMonthFinancials

    fields = PropertyMonthFinancials.model_fields
    for name in (
        "property_name",
        "reporting_month",
        "total_units",
        "occupied_units",
        "gross_potential_rent",
        "vacancy_loss",
        "concessions",
        "bad_debt",
        "other_income",
        "effective_gross_income",
    ):
        assert name in fields


def test_skill_is_instructions_only():
    from financial_rlm import financial_extraction_skill

    assert financial_extraction_skill.name == "financial-extraction"
    assert "Gross Potential Rent" in financial_extraction_skill.instructions
    assert not financial_extraction_skill.packages
    assert not financial_extraction_skill.tools
