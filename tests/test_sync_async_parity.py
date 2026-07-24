"""Structural parity between PredictRLM's sync and async twins.

PredictRLM carries four hand-mirrored sync/async pairs. Every one of them
has to be edited twice, and a miss is silent: the sync path keeps working
while ``aforward()`` quietly diverges (or vice versa), and only shows up as
a behavior difference under whichever entry point the caller happened to
use. The 2.6 extract-fallback metering had to land in both loops by hand
for exactly this reason.

These tests normalize away the things a sync/async pair is ALLOWED to
differ by — ``await``, ``async def``/``async with``, and the ``_a`` name
prefix on the twin helpers — and then require the remaining structure to
be identical. Anything left over is unintended drift.

Where a pair legitimately cannot be normalized to identity (the callback
scopes wrap a ``yield``; the iteration executors differ in how they catch
interpreter errors), the shared logic is asserted to live in a common
helper instead, so it can only be written once.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from predict_rlm.predict_rlm import PredictRLM

# Async twins whose bodies must normalize to their sync counterpart.
#
# ``_execute_iteration`` is deliberately absent: its twin diverges for real
# reasons that normalization must not paper over — it probes for
# ``aexecute`` before falling back to the blocking ``execute``, and it
# catches a wider set of interpreter errors. Those pairs are covered by
# test_divergent_pairs_share_their_payload instead.
MIRRORED_PAIRS = [
    ("_forward_traced", "_aforward_traced"),
    ("_run_extract_fallback", "_arun_extract_fallback"),
]

# Pairs that legitimately keep two skeletons, with the reason they can't
# merge. Their shared payload must still live in common helpers.
DIVERGENT_PAIRS = [
    pytest.param(
        "_iteration_callback_scope",
        "_aiteration_callback_scope",
        id="callback-scope-wraps-a-yield",
    ),
    pytest.param(
        "_execute_iteration",
        "_aexecute_iteration",
        id="iteration-differs-in-interpreter-dispatch",
    ),
]

# ``_a``-prefixed helpers that are the async twin of the same name without
# the prefix. Renaming these is part of normalization, not drift.
TWIN_RENAMES = {
    "_aforward_traced": "_forward_traced",
    "_aexecute_iteration": "_execute_iteration",
    "_arun_extract_fallback": "_run_extract_fallback",
    "_aiteration_callback_scope": "_iteration_callback_scope",
    "_aextract_fallback": "_extract_fallback",
    "_dispatch_async": "_dispatch_sync",
    "acall": "__call__",
}


class _Desyncer(ast.NodeTransformer):
    """Rewrite an async function into its sync shape."""

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        self.generic_visit(node)
        return ast.FunctionDef(
            name=node.name,
            args=node.args,
            body=node.body,
            decorator_list=node.decorator_list,
            returns=node.returns,
            type_comment=None,
            type_params=[],
        )

    def visit_AsyncWith(self, node: ast.AsyncWith) -> ast.AST:
        self.generic_visit(node)
        return ast.With(items=node.items, body=node.body, type_comment=None)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> ast.AST:
        self.generic_visit(node)
        return ast.For(
            target=node.target,
            iter=node.iter,
            body=node.body,
            orelse=node.orelse,
            type_comment=None,
        )

    def visit_Await(self, node: ast.Await) -> ast.AST:
        self.generic_visit(node)
        return node.value

    def visit_Name(self, node: ast.Name) -> ast.AST:
        node.id = TWIN_RENAMES.get(node.id, node.id)
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        node.attr = TWIN_RENAMES.get(node.attr, node.attr)
        return node


def _normalized(method_name: str) -> str:
    """Source of one method, reduced to sync structure with names aligned.

    Docstrings and comments are dropped — a sync/async pair is expected to
    describe itself differently.
    """
    func = getattr(PredictRLM, method_name)
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    tree = _Desyncer().visit(tree)
    ast.fix_missing_locations(tree)

    body = tree.body[0]
    body.name = TWIN_RENAMES.get(body.name, body.name)
    if (
        body.body
        and isinstance(body.body[0], ast.Expr)
        and isinstance(body.body[0].value, ast.Constant)
        and isinstance(body.body[0].value.value, str)
    ):
        body.body = body.body[1:]
    return ast.dump(tree, indent=2)


@pytest.mark.parametrize(("sync_name", "async_name"), MIRRORED_PAIRS)
def test_async_twin_is_structurally_identical(sync_name: str, async_name: str):
    """The twin must differ ONLY by await/async and the ``_a`` prefix.

    A failure here means the pair drifted: some logic was added to one
    side and not the other, or logic that belongs in a shared helper was
    inlined into both and then edited unevenly.
    """
    sync_src = _normalized(sync_name)
    async_src = _normalized(async_name)

    assert sync_src == async_src, (
        f"{sync_name} and {async_name} have diverged beyond await/async. "
        "Move the shared logic into a helper both call, rather than "
        "editing two copies."
    )


def _self_helper_calls(method_name: str) -> list[str]:
    """``self._helper(...)`` calls made by one method, in source order.

    Only private helpers count — public collaborators like
    ``self.generate_action`` are the very thing the twins are allowed to
    invoke differently.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(PredictRLM, method_name))))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        target = node.func.value
        name = TWIN_RENAMES.get(node.func.attr, node.func.attr)
        if isinstance(target, ast.Name) and target.id == "self" and name.startswith("_"):
            calls.append((node.lineno, node.col_offset, name))
    return [name for _, _, name in sorted(calls)]


@pytest.mark.parametrize(("sync_name", "async_name"), DIVERGENT_PAIRS)
def test_divergent_pairs_share_their_payload(sync_name: str, async_name: str):
    """Pairs kept separate on purpose still must not duplicate logic.

    ``_iteration_callback_scope`` wraps a ``yield`` — a sync generator and
    an async generator can't be one object — and ``_execute_iteration``
    dispatches to the interpreter differently. Those are real reasons to
    keep two skeletons. They are not a reason to write the payload twice:
    the shared work each skeleton performs must come from the same helpers,
    called in the same order, so there is only one copy to edit.
    """
    sync_calls = _self_helper_calls(sync_name)
    async_calls = _self_helper_calls(async_name)

    assert sync_calls == async_calls, (
        f"{sync_name} and {async_name} no longer delegate to the same "
        "helpers in the same order — logic was added to one skeleton "
        "without the other."
    )
    assert sync_calls, (
        f"{sync_name} / {async_name} share no helper calls — their common "
        "logic is inlined twice and will drift."
    )
