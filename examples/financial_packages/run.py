"""Run the financial packages normalization example.

Drop monthly Excel financial packages into `sample/input/` next to this script,
then run:

    uv run examples/financial_packages/run.py
    uv run examples/financial_packages/run.py march.xlsx april.xlsx
    uv run examples/financial_packages/run.py /path/to/packages/
    uv run examples/financial_packages/run.py --debug

Requires:
    pip install 'predict-rlm[examples]'

Environment:
    Set OPENAI_API_KEY (or whatever LLM provider you configure below).
"""

import argparse
import asyncio
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import dspy

from predict_rlm import File

sys.path.insert(0, str(Path(__file__).resolve().parent))

from financial_rlm import FinancialPackageNormalizer

SOURCE_DIR = Path(__file__).parent / "sample" / "input"
LLM_MODEL = "openai/gpt-5.4"
SUB_LM_MODEL = "openai/gpt-5.1"


def get_model_config(model: str):
    if model == "openai/gpt-5.4":
        return dict(model=model, num_retries=5, reasoning_effort="none")
    return dict(model=model)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Normalize Excel financial packages into one workbook"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print REPL code, output, errors, and tool calls to stderr",
    )
    parser.add_argument(
        "--model",
        default=LLM_MODEL,
        help=f"Main LM to use (default: {LLM_MODEL})",
    )
    parser.add_argument(
        "--sub-lm-model",
        default=SUB_LM_MODEL,
        help=f"Sub-LM to use (default: {SUB_LM_MODEL})",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=40,
        help="Maximum REPL iterations (default: 40)",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Excel files or directories to process (default: sample/input/)",
    )
    return parser.parse_args()


def discover_packages(file_args: list[str]) -> list[Path]:
    patterns = ("*.xlsx", "*.xlsm")
    if not file_args:
        found: list[Path] = []
        for pat in patterns:
            found.extend(SOURCE_DIR.glob(pat))
        return sorted(found)

    packages: list[Path] = []
    for f in file_args:
        p = Path(f)
        if not p.exists():
            print(f"File not found: {p}")
            sys.exit(1)
        if p.is_dir():
            for pat in patterns:
                packages.extend(sorted(p.glob(pat)))
        else:
            packages.append(p)
    return packages


async def main():
    args = parse_args()

    packages = discover_packages(args.files)
    if not packages:
        print(f"No Excel files found in {SOURCE_DIR.resolve()}")
        print("Drop some .xlsx packages there, or pass file paths as arguments.")
        return

    print(f"Found {len(packages)} package(s):")
    for p in packages:
        print(f"  - {p.name}")
    print()

    lm = dspy.LM(**get_model_config(args.model), cache=False)
    sub_lm = dspy.LM(args.sub_lm_model, cache=False)

    files = [File(path=str(p.resolve())) for p in packages]

    print("Normalizing financial packages...")
    print("-" * 60)

    normalizer = FinancialPackageNormalizer(
        sub_lm=sub_lm,
        max_iterations=args.max_iterations,
        verbose=True,
        debug=args.debug,
    )
    start_time = time.perf_counter()
    with dspy.context(lm=lm):
        prediction = await normalizer.aforward(packages=files)
    run_duration = time.perf_counter() - start_time

    result = prediction.result

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(__file__).parent / "output" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    workbook_path = prediction.workbook.path
    if workbook_path:
        for f in Path(workbook_path).parent.glob("*.xlsx"):
            dest = output_dir / f.name
            shutil.copy2(f, dest)
            print(f"Output file: {dest}")

    print()
    print("=" * 60)
    print("NORMALIZATION RESULTS")
    print("=" * 60)
    print(result.summary)
    print()

    for rec in result.records:
        print(f"  {rec.property_name} — {rec.reporting_month}  [{rec.source_file}]")
        print(
            f"    Units: {rec.occupied_units}/{rec.total_units}  "
            f"GPR: {rec.gross_potential_rent}  EGI: {rec.effective_gross_income}"
        )
    print()

    lm_history = list(lm.history)
    sub_lm_history = list(sub_lm.history)
    lm_cost = sum(e.get("cost", 0) or 0 for e in lm_history)
    sub_lm_cost = sum(e.get("cost", 0) or 0 for e in sub_lm_history)
    total_cost = lm_cost + sub_lm_cost
    mins, secs = divmod(int(run_duration), 60)

    print("=" * 60)
    print("RUN STATS")
    print("=" * 60)
    print(f"Main LM:   {args.model}")
    print(f"Sub-LM:    {args.sub_lm_model}")
    print(f"Packages:  {len(packages)}")
    print(f"Records:   {len(result.records)}")
    print(f"Duration:  {mins}m {secs}s")
    print(f"Main LM ({len(lm_history)} calls):  ${lm_cost:.4f}")
    print(f"Sub-LM ({len(sub_lm_history)} calls): ${sub_lm_cost:.4f}")
    print(f"Total cost: ${total_cost:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
