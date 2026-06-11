import dspy

from predict_rlm import File

from .schema import FinancialExtractionResult


class ExtractFinancialPackages(dspy.Signature):
    """Extract property/month financial line items from monthly Excel financial
    packages into a single normalized Excel workbook.

    Each input file is a monthly financial package. A package may cover one
    property or several, and may contain many sheets (operating statement, rent
    roll, T-12, budget vs. actual, etc.). Property name and reporting month must
    be read from *inside* the workbook, never inferred from the file name.

    1. **Survey the inputs.** The files are mounted at
       `/sandbox/input/packages/`. For each file, open it with openpyxl
       (`load_workbook(path, data_only=True, read_only=True)`) and list sheet
       names and dimensions. Print a short inventory. Use `data_only=True` so
       you read cached values rather than formula strings.

    2. **Locate the operating statement.** For each workbook, find the sheet(s)
       that contain the income lines (Gross Potential Rent, Vacancy, Other
       Income, Effective Gross Income). Skip rent rolls and amortization
       schedules unless they hold the unit counts.

    3. **Identify property and reporting month from inside the sheet.** Look in
       title rows, headers, and labeled cells. Reporting month is often a column
       header (e.g. "Mar-2025", "March 2025", "Period Ending 3/31/2025") or a
       title cell. If a sheet has many month columns (a T-12), pick the single
       reporting month the package is for (usually the latest actual month or
       the one named in the title). Normalize the month to `YYYY-MM`.

    4. **Map labels to the canonical metrics.** Row labels vary by owner. Dump
       the candidate label cells and their row values, then use `predict()` to
       map them to the canonical fields, handling synonyms and abbreviations.
       Read the financial-extraction skill for the synonym list and sign rules.
       Extract: Total Units, Occupied Units, Gross Potential Rent, Vacancy Loss,
       Concessions, Bad Debt / Credit Loss, Other Income, Effective Gross
       Income. Store deductions (vacancy, concessions, bad debt) as positive
       magnitudes. Leave a field null only when it is genuinely absent.

    5. **Process workbooks/properties in parallel** with `asyncio.gather()` when
       there are several, but keep each `predict()` call scoped to one
       property/month so its context stays small.

    6. **Sanity-check each record.** Where the inputs allow it, verify
       `EGI ≈ GPR - Vacancy - Concessions - BadDebt + OtherIncome` and that
       Occupied Units ≤ Total Units. Note material discrepancies in the summary
       rather than silently overwriting reported figures.

    7. **Build the normalized workbook** with openpyxl: one sheet named
       `Normalized` with a header row and one row per (property, reporting
       month), columns in this order: Property, Reporting Month, Total Units,
       Occupied Units, Gross Potential Rent, Vacancy Loss, Concessions,
       Bad Debt / Credit Loss, Other Income, Effective Gross Income, Source
       File. Sort rows by property then month. Use accounting number formats for
       dollar columns, integers for unit columns, and bold the header row.
       Auto-size columns. Save to `/sandbox/output/workbook/normalized.xlsx`.

    8. **Return the result**: the structured records and a summary describing
       which properties/months and files were processed and any gaps.
    """

    packages: list[File] = dspy.InputField(
        desc="Excel financial package files (.xlsx), mounted at /sandbox/input/packages/"
    )
    workbook: File = dspy.OutputField(
        desc="Normalized Excel workbook (.xlsx) with one row per property/reporting month"
    )
    result: FinancialExtractionResult = dspy.OutputField(
        desc="Structured normalized records plus a processing summary"
    )
