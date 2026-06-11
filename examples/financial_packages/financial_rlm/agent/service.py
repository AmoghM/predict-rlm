"""FinancialPackageNormalizer — RLM service for normalizing Excel financial packages.

Usage::

    from predict_rlm import File

    packages = [File(path="march_package.xlsx"), File(path="april_package.xlsx")]
    normalizer = FinancialPackageNormalizer(sub_lm="openai/gpt-5.1")
    prediction = await normalizer.aforward(packages=packages)
    # prediction.result   — FinancialExtractionResult with one record per property/month
    # prediction.workbook — File with the normalized Excel workbook
"""

import dspy

from predict_rlm import File, PredictRLM
from predict_rlm.skills import spreadsheet as spreadsheet_skill

from .signature import ExtractFinancialPackages
from .skills import financial_extraction_skill


class FinancialPackageNormalizer(dspy.Module):
    """DSPy Module that wraps ExtractFinancialPackages + PredictRLM."""

    def __init__(
        self,
        lm: dspy.LM | str | None = None,
        sub_lm: dspy.LM | str | None = None,
        max_iterations: int = 40,
        verbose: bool = False,
        debug: bool = False,
    ):
        self.lm = lm
        self.sub_lm = sub_lm
        self.max_iterations = max_iterations
        self.verbose = verbose
        self.debug = debug

    async def aforward(self, packages: list[File]):
        """Normalize the financial packages and return the prediction.

        Returns a dspy.Prediction with:
        - result: FinancialExtractionResult with per-property/month records
        - workbook: File with the normalized Excel workbook
        """
        predictor = PredictRLM(
            ExtractFinancialPackages,
            lm=self.lm,
            sub_lm=self.sub_lm,
            skills=[spreadsheet_skill, financial_extraction_skill],
            max_iterations=self.max_iterations,
            verbose=self.verbose,
            debug=self.debug,
        )
        return await predictor.acall(packages=packages)
