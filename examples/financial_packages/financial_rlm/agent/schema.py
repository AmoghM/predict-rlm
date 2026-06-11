from pydantic import BaseModel, Field


class PropertyMonthFinancials(BaseModel):
    """Normalized financial line items for one property in one reporting month.

    Deduction lines (vacancy, concessions, bad debt) are stored as positive
    magnitudes regardless of how the source workbook signs them, so downstream
    consumers can apply a single convention:
    ``EGI = GPR - Vacancy - Concessions - BadDebt + OtherIncome``.
    """

    property_name: str = Field(
        description="Property name as written inside the workbook, not the file name"
    )
    reporting_month: str = Field(
        description="Reporting month as 'YYYY-MM' (e.g. '2025-03'), read from inside the workbook"
    )
    source_file: str = Field(
        description="File name the figures were extracted from, for traceability"
    )
    total_units: float | None = Field(
        default=None, description="Total unit count for the property"
    )
    occupied_units: float | None = Field(
        default=None, description="Occupied unit count for the reporting month"
    )
    gross_potential_rent: float | None = Field(
        default=None, description="Gross Potential Rent (GPR) in dollars"
    )
    vacancy_loss: float | None = Field(
        default=None,
        description="Vacancy loss in dollars, as a positive magnitude (a deduction from GPR)",
    )
    concessions: float | None = Field(
        default=None,
        description="Concessions in dollars, as a positive magnitude (a deduction from GPR)",
    )
    bad_debt: float | None = Field(
        default=None,
        description="Bad Debt / Credit Loss in dollars, as a positive magnitude (a deduction from GPR)",
    )
    other_income: float | None = Field(
        default=None, description="Other income in dollars (added to net rental income)"
    )
    effective_gross_income: float | None = Field(
        default=None, description="Effective Gross Income (EGI) in dollars"
    )


class FinancialExtractionResult(BaseModel):
    """Result of normalizing one or more Excel financial packages."""

    records: list[PropertyMonthFinancials] = Field(
        description="One record per (property, reporting month) found across all inputs"
    )
    summary: str = Field(
        description="Brief summary: properties/months found, files processed, and any gaps or assumptions"
    )
