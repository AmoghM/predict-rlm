# Financial Packages Normalizer

Extract property/month financial line items from monthly Excel financial
packages into a single normalized Excel workbook. For each property and
reporting month found *inside* the workbooks, it extracts: Total Units, Occupied
Units, Gross Potential Rent, Vacancy Loss, Concessions, Bad Debt / Credit Loss,
Other Income, and Effective Gross Income.

Property name and reporting month are read from inside each workbook, not from
the file name.

## Setup

```bash
git clone https://github.com/Trampoline-AI/predict-rlm.git
cd predict-rlm
uv sync --extra examples
export OPENAI_API_KEY=sk-...
```

## Usage

```bash
# Run with packages dropped in sample/input/
uv run examples/financial_packages/run.py

# Pass your own files (a file, list of files, or a folder)
uv run examples/financial_packages/run.py march.xlsx april.xlsx
uv run examples/financial_packages/run.py /path/to/packages/

# With debug output (prints REPL code and tool calls)
uv run examples/financial_packages/run.py --debug
```

### Options

| Flag               | Default          | Description                   |
| ------------------ | ---------------- | ----------------------------- |
| `--model`          | `openai/gpt-5.4` | Main LM (writes/runs code)    |
| `--sub-lm-model`   | `openai/gpt-5.1` | Sub-LM for `predict()` calls  |
| `--max-iterations` | `40`             | Max REPL iterations           |
| `--debug`          | off              | Print REPL activity to stderr |

The normalized workbook and the structured summary are saved to
`output/{timestamp}/` inside this directory.

## Output

A single `.xlsx` with a `Normalized` sheet — one row per (property, reporting
month):

| Property | Reporting Month | Total Units | Occupied Units | Gross Potential Rent | Vacancy Loss | Concessions | Bad Debt / Credit Loss | Other Income | Effective Gross Income | Source File |
| -------- | --------------- | ----------- | -------------- | -------------------- | ------------ | ----------- | ---------------------- | ------------ | ---------------------- | ----------- |

Deduction lines (vacancy, concessions, bad debt) are normalized to positive
magnitudes so `EGI = GPR − Vacancy − Concessions − BadDebt + OtherIncome`.

## Structure

| File                                       | Purpose                                                        |
| ------------------------------------------ | ------------------------------------------------------------- |
| [`agent/schema.py`](financial_rlm/agent/schema.py)       | Pydantic models for normalized records      |
| [`agent/signature.py`](financial_rlm/agent/signature.py) | DSPy Signature with the extraction strategy |
| [`agent/skills.py`](financial_rlm/agent/skills.py)       | Domain skill: label synonyms + sign rules   |
| [`agent/service.py`](financial_rlm/agent/service.py)     | DSPy Module wiring PredictRLM with skills    |
| [`run.py`](run.py)                         | CLI entry point                                               |
| [`tests/test_smoke.py`](tests/test_smoke.py)             | Fast no-network import/construction tests   |

## Notes & assumptions

- Inputs are `.xlsx` / `.xlsm` (openpyxl-readable). Legacy `.xls` is not
  supported by the sandbox readers; convert to `.xlsx` first.
- A single workbook may contain multiple properties and/or sheets; each
  (property, reporting month) becomes its own row.
- Reads cached cell values (`data_only=True`). If a package ships without cached
  values, the RLM falls back to evaluating formulas in-sandbox.
