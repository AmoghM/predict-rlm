"""RED-GREEN repro for unbounded host-side tool calls.

Background:
    Sandbox code calling ``await recalculate(path)`` triggers a host
    tool dispatch in ``JspiInterpreter._execute_tool_async``. If the
    tool is slow (e.g. LibreOffice on a whole-column formula → 2-3
    minutes), the overall ``execute`` round-trip blows past the
    ``_exec_timeout`` ceiling. That timeout **kills the Deno
    subprocess**, raises ``SandboxFatalError``, and turns what should
    have been a recoverable tool error into a cascade of
    ``[Errno 9] Bad file descriptor`` retries. A 2026-04-18 gemini
    eval lost 19 cases to this failure mode — every one of them
    scored 0 by the time ``task_timeout=600s`` finally fired.

    The fix: give each tool call its own wall-clock budget
    (``TOOL_CALL_TIMEOUT_SEC``, default 180s) via ``asyncio.wait_for``.
    If the tool exceeds its budget, return a clean error response to
    the sandbox — deno's ``await tool()`` resumes with the error,
    exec continues, the RLM can see "[Error] tool timed out" and
    rewrite its code using a different approach. The sandbox stays
    alive, the case stays recoverable.

RED: a mock tool that sleeps forever hangs ``_execute_tool_async``.
GREEN: it returns an error response with a timeout message within
    ~TOOL_CALL_TIMEOUT_SEC.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import sys
import threading
import time
from pathlib import Path

import pytest

import predict_rlm.interpreter as rlm_interpreter
from predict_rlm.interpreter import JspiInterpreter
from predict_rlm.interpreters import SbxConfig, SbxInterpreter

RUNNER_PATH = Path(__file__).parents[1] / "src" / "predict_rlm" / "sandbox" / "python_runner.py"


def _build_interp_with_tool(tool_fn, tool_name: str = "slow_tool"):
    interp = JspiInterpreter.__new__(JspiInterpreter)
    interp.tools = {tool_name: tool_fn}
    interp._debug = False
    interp._executor = None  # only used for sync tools
    # Bypass the SyncedFile param-scanner by setting no tools use it.
    # The get_synced_file_params() helper returns {} for a plain fn.
    return interp


async def _async_never_returns(*args, **kwargs):
    """Async tool that hangs forever — the pathological case our fix
    must bound."""
    await asyncio.sleep(3600)
    return "unreachable"  # pragma: no cover


def test_async_tool_that_hangs_times_out_cleanly(monkeypatch):
    """A tool that never returns must surface as an error response
    within ~TOOL_CALL_TIMEOUT_SEC, not hang the caller indefinitely.
    """
    monkeypatch.setattr(rlm_interpreter, "TOOL_CALL_TIMEOUT_SEC", 0.3)

    interp = _build_interp_with_tool(_async_never_returns)
    t0 = time.monotonic()

    async def _run():
        # Outer safety net: if the fix isn't in, asyncio.wait_for here
        # turns the hang into a TimeoutError so pytest can fail
        # cleanly instead of running forever.
        return await asyncio.wait_for(
            interp._execute_tool_async("slow_tool", {"args": [], "kwargs": {}}),
            timeout=5.0,
        )

    try:
        response = asyncio.run(_run())
    except asyncio.TimeoutError:
        pytest.fail(
            "_execute_tool_async hung past the test's 5s safety timeout — "
            "the per-tool TOOL_CALL_TIMEOUT_SEC bound isn't enforced"
        )

    elapsed = time.monotonic() - t0
    assert elapsed < 2.0, (
        f"tool returned but took {elapsed:.2f}s — expected ~0.3s based on "
        f"the monkeypatched timeout"
    )
    assert "error" in response, (
        f"expected error response after timeout, got {response!r}"
    )
    err = str(response.get("error") or "")
    assert "timed out" in err.lower() or "timeout" in err.lower(), (
        f"error message should mention the timeout; got {err!r}"
    )


def test_async_tool_that_completes_quickly_is_not_affected(monkeypatch):
    """Guardrail: normal fast tools must continue to return their
    results unchanged — the timeout is a ceiling, not a delay.
    """
    monkeypatch.setattr(rlm_interpreter, "TOOL_CALL_TIMEOUT_SEC", 1.0)

    async def _fast_tool(**_kwargs):
        return "ok"

    interp = _build_interp_with_tool(_fast_tool)
    response = asyncio.run(
        interp._execute_tool_async("slow_tool", {"args": [], "kwargs": {}})
    )
    assert response.get("value") == "ok"
    assert "error" not in response


def test_tool_exception_still_routes_through_error_path(monkeypatch):
    """If a tool raises (e.g. ValueError inside the tool), the existing
    ``except Exception`` in _execute_tool_async captures it and returns
    ``{"error": ...}``. The timeout wrap must not change this behaviour.
    """
    monkeypatch.setattr(rlm_interpreter, "TOOL_CALL_TIMEOUT_SEC", 1.0)

    async def _raising_tool(**_kwargs):
        raise ValueError("tool blew up")

    interp = _build_interp_with_tool(_raising_tool)
    response = asyncio.run(
        interp._execute_tool_async("slow_tool", {"args": [], "kwargs": {}})
    )
    assert "error" in response
    assert "blew up" in str(response["error"])


def test_sync_tool_timeout_does_not_poison_executor(monkeypatch):
    monkeypatch.setattr(rlm_interpreter, "TOOL_CALL_TIMEOUT_SEC", 0.05)

    def _slow_tool():
        time.sleep(0.5)
        return "unreachable"

    def _fast_tool():
        return "ok"

    interp = _build_interp_with_tool(_slow_tool)
    interp._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        first = asyncio.run(
            interp._execute_tool_async("slow_tool", {"args": [], "kwargs": {}})
        )
        assert "error" in first

        interp.tools["slow_tool"] = _fast_tool
        started = time.monotonic()
        second = asyncio.run(
            interp._execute_tool_async("slow_tool", {"args": [], "kwargs": {}})
        )

        assert second.get("value") == "ok"
        assert time.monotonic() - started < 0.3
    finally:
        interp._executor.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# SBX backend parity
#
# The SBX interpreter had NO per-tool-call bound at all: ``_build_tool_response``
# called ``tool(*args, **kwargs)`` directly in a ThreadPoolExecutor thread and
# the only ceiling was the whole-request ``exec_timeout``, which KILLS the
# runner process (``_fail_timed_out_request`` -> ``SandboxFatalError``). A slow
# tool therefore destroyed the sandbox instead of producing a recoverable
# error, the exact failure mode the JSPI tests above exist to prevent.
# ---------------------------------------------------------------------------


class TestSbxToolCallTimeout:
    def make_interpreter(self, tmp_path: Path, tools: dict, **config) -> SbxInterpreter:
        return SbxInterpreter(
            config=SbxConfig(name="tool-timeout-test", **config),
            tools=tools,
            preinstall_packages=False,
            _runner_command=[sys.executable, "-u", str(RUNNER_PATH)],
            _staging_root=tmp_path / "staging",
        )

    def test_default_tool_call_timeout_matches_jspi(self):
        """SBX's default per-tool budget matches JSPI's 180s."""
        assert SbxConfig().tool_call_timeout == rlm_interpreter.TOOL_CALL_TIMEOUT_SEC

    def test_tool_budget_stays_below_exec_timeout(self):
        """The per-tool guard is useless unless it fires BEFORE the
        whole-request wall that kills the runner, so a misconfigured
        (or merely default) tool budget is clamped under exec_timeout.
        """
        interp = SbxInterpreter.__new__(SbxInterpreter)
        interp.config = SbxConfig(exec_timeout=10, tool_call_timeout=180)
        assert interp._tool_call_budget() < 10

        interp.config = SbxConfig(exec_timeout=300, tool_call_timeout=180)
        assert interp._tool_call_budget() == 180

    def test_hanging_tool_is_recoverable_and_sandbox_survives(self, tmp_path: Path):
        """A wedged tool must surface as a normal in-sandbox error and
        leave the runner alive for the next call — not trip exec_timeout
        and kill the process.
        """
        release = threading.Event()

        def hang() -> str:
            release.wait(30)
            return "unreachable"

        def quick() -> str:
            return "quick"

        interpreter = self.make_interpreter(
            tmp_path,
            {"hang": hang, "quick": quick},
            exec_timeout=20,
            tool_call_timeout=0.3,
        )
        try:
            started = time.monotonic()
            output = interpreter.execute(
                "try:\n"
                "    await hang()\n"
                "except Exception as exc:\n"
                "    print('TOOLERR:', exc)\n"
            )
            elapsed = time.monotonic() - started

            assert "TOOLERR:" in output, f"tool error never reached the sandbox: {output!r}"
            assert "timed out" in output.lower(), output
            assert elapsed < 10, (
                f"took {elapsed:.1f}s — the per-tool budget did not fire"
            )

            # Sandbox survived: a subsequent call still works, and the
            # interpreter state from before the hang is intact.
            assert interpreter.execute("print(await quick())").strip() == "quick"
        finally:
            release.set()
            interpreter.shutdown()

    def test_hung_tool_does_not_starve_later_tool_calls(self, tmp_path: Path):
        """The abandoned thread can't be killed, so the executor must be
        recycled — otherwise repeated hangs exhaust the worker pool.
        """
        release = threading.Event()

        def hang() -> str:
            release.wait(30)
            return "unreachable"

        def quick() -> str:
            return "quick"

        interpreter = self.make_interpreter(
            tmp_path,
            {"hang": hang, "quick": quick},
            exec_timeout=20,
            tool_call_timeout=0.3,
        )
        try:
            # Wedge every worker in the original pool.
            for _ in range(6):
                interpreter.execute(
                    "try:\n    await hang()\nexcept Exception:\n    pass\n"
                )
            started = time.monotonic()
            assert interpreter.execute("print(await quick())").strip() == "quick"
            assert time.monotonic() - started < 3, "later tool call was starved"
        finally:
            release.set()
            interpreter.shutdown()

    def test_quick_tool_is_unaffected_by_the_budget(self, tmp_path: Path):
        """The budget is a ceiling, not a delay."""

        def quick(value: int) -> dict:
            return {"doubled": value * 2}

        interpreter = self.make_interpreter(
            tmp_path, {"quick": quick}, exec_timeout=20, tool_call_timeout=5
        )
        try:
            started = time.monotonic()
            output = interpreter.execute(
                "result = await quick(21)\nprint(result['doubled'])"
            )
        finally:
            interpreter.shutdown()

        assert output.strip() == "42"
        assert time.monotonic() - started < 5

    def test_tool_exception_still_routes_as_error_not_timeout(self, tmp_path: Path):
        """A raising tool keeps its own message; the timeout wrap must
        not swallow or relabel it.
        """

        def boom() -> str:
            raise ValueError("tool blew up")

        interpreter = self.make_interpreter(
            tmp_path, {"boom": boom}, exec_timeout=20, tool_call_timeout=5
        )
        try:
            output = interpreter.execute(
                "try:\n"
                "    await boom()\n"
                "except Exception as exc:\n"
                "    print('TOOLERR:', exc)\n"
            )
        finally:
            interpreter.shutdown()

        assert "blew up" in output
        assert "timed out" not in output.lower()
