from predict_rlm import Skill

financial_extraction_skill = Skill(
    name="financial-extraction",
    instructions="""# Real-estate financial package extraction

You are normalizing monthly operating statements from multifamily / commercial
real-estate financial packages. Labels and layouts vary by owner and accounting
system; map what you read to the canonical metrics below.

## Canonical metrics and common synonyms

- **Total Units**: "Total Units", "Unit Count", "# Units", "Units", "Total
  Apartments". A count, not a dollar figure.
- **Occupied Units**: "Occupied Units", "Occupied", "Leased Units", "Physical
  Occupancy (units)". A count. If only an occupancy *percentage* is given,
  compute occupied = round(total_units * pct) and note it in the summary.
- **Gross Potential Rent (GPR)**: "Gross Potential Rent", "GPR", "Gross
  Potential Income", "GPI", "Market Rent", "Scheduled Rent", "Potential Rent".
- **Vacancy Loss**: "Vacancy", "Vacancy Loss", "Loss to Vacancy", "Physical
  Vacancy", "Vacancy/Loss".
- **Concessions**: "Concessions", "Rent Concessions", "Loss to Concessions",
  "Discounts".
- **Bad Debt / Credit Loss**: "Bad Debt", "Credit Loss", "Bad Debt/Credit
  Loss", "Collection Loss", "Uncollectible".
- **Other Income**: "Other Income", "Ancillary Income", "Miscellaneous Income",
  "Total Other Income". Includes fees, parking, laundry, RUBS, etc. Prefer a
  reported subtotal over summing components yourself; if you must sum, note it.
- **Effective Gross Income (EGI)**: "Effective Gross Income", "EGI", "Total
  Income", "Net Rental Income + Other Income", "Total Operating Income".

## Sign conventions

Vacancy, concessions, and bad debt are deductions from GPR. Source workbooks
report them inconsistently — sometimes as negative numbers, sometimes wrapped in
parentheses, sometimes as positive magnitudes. Always store them as **positive
magnitudes** in the output. The canonical relationship is:

    EGI = GPR - Vacancy - Concessions - BadDebt + OtherIncome

Use this only to sanity-check; do not back-solve a missing reported figure
unless you flag it in the summary.

## Reading values reliably

- Open with `load_workbook(path, data_only=True, read_only=True)` so you read
  cached results, not formula text. Most exported packages cache their values,
  so this is enough. If `data_only` cells come back as `None` across the board,
  the file was never recalculated by Excel: reopen without `data_only` to read
  the formula, evaluate it with the mounted `formula_eval` module (from the
  spreadsheet skill), or fall back to a `recalculate(path)` tool if one is
  available in the environment.
- Parentheses mean negative: `(12,345)` -> -12345. Strip `$`, commas, and
  trailing spaces before converting to float.
- Distinguish counts (units) from dollars by column/label context, not by
  magnitude alone.
- Ignore YTD, budget, variance, and prior-year columns unless the package's
  reporting month *is* a YTD figure. Extract the single reporting-month
  (current actual) column.

## Reporting month

- Read it from inside the sheet: title cells, period labels, or the actual
  month column header. Accept formats like "Mar-25", "March 2025",
  "3/31/2025", "Period: 03/2025", "For the Month Ending March 31, 2025".
- Normalize to `YYYY-MM`. For a T-12 / trailing-twelve sheet, the reporting
  month is the package's stated current month (usually the rightmost actual
  column or the one named in the title), not all twelve.

## Property identity

- The property name lives inside the workbook (title block, header row, or a
  labeled "Property" cell), not in the file name.
- A single workbook may contain multiple properties (one per sheet or one per
  block). Emit a separate record for each (property, reporting month).
- If genuinely unavailable inside the workbook, fall back to the file stem and
  flag the assumption in the summary.

## Using predict() for label mapping

When labels are ambiguous, dump the candidate label cells with their row values
and let the sub-LM map them, e.g.:

    await predict(
        "rows: str -> mapping: dict[str, str]",
        instructions="Map each source row label to one canonical metric name "
        "from this set, or 'ignore': total_units, occupied_units, "
        "gross_potential_rent, vacancy_loss, concessions, bad_debt, "
        "other_income, effective_gross_income.",
        rows=labelled_rows_text,
    )

Keep each predict() call scoped to one property/month so its context stays
small.
""",
)

__all__ = ["financial_extraction_skill"]
