"""Shared interpreter backend types."""

from __future__ import annotations

import asyncio
import contextvars
import threading
from contextlib import asynccontextmanager, contextmanager
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

DEFAULT_SBX_TEMPLATE = "docker.io/docker/sandbox-templates:shell"
STALE_RESPONSE_DISCARD_LIMIT = 50

_TOOL_CALLBACK_GATES: contextvars.ContextVar[frozenset[int]] = (
    contextvars.ContextVar("_TOOL_CALLBACK_GATES", default=frozenset())
)
_THREAD_LOCAL_TOOL_CALLBACKS = threading.local()


class InterpreterExecutionGate:
    """Serialize top-level interpreter execution and reject tool reentry."""

    def __init__(self, interpreter_name: str) -> None:
        self._interpreter_name = interpreter_name
        self._condition = threading.Condition()
        self._running = False

    @contextmanager
    def top_level(self):
        self._raise_if_in_tool_callback()
        self._acquire()
        try:
            yield
        finally:
            self._release()

    @asynccontextmanager
    async def atop_level(self):
        self._raise_if_in_tool_callback()
        await self._acquire_async()
        try:
            yield
        finally:
            self._release()

    @contextmanager
    def tool_callback(self):
        token = self._enter_contextvar_tool_callback()
        stack = getattr(_THREAD_LOCAL_TOOL_CALLBACKS, "stack", ())
        _THREAD_LOCAL_TOOL_CALLBACKS.stack = (*stack, id(self))
        try:
            yield
        finally:
            _THREAD_LOCAL_TOOL_CALLBACKS.stack = stack
            _TOOL_CALLBACK_GATES.reset(token)

    @contextmanager
    def async_tool_callback(self):
        token = self._enter_contextvar_tool_callback()
        try:
            yield
        finally:
            _TOOL_CALLBACK_GATES.reset(token)

    def _acquire(self) -> None:
        with self._condition:
            while self._running:
                self._condition.wait()
            self._running = True

    async def _acquire_async(self) -> None:
        while True:
            with self._condition:
                if not self._running:
                    self._running = True
                    return
            await asyncio.sleep(0.01)

    def _release(self) -> None:
        with self._condition:
            self._running = False
            self._condition.notify_all()

    def _raise_if_in_tool_callback(self) -> None:
        gate_id = id(self)
        if gate_id in _TOOL_CALLBACK_GATES.get():
            raise RuntimeError(self._tool_reentry_message())
        if gate_id in getattr(_THREAD_LOCAL_TOOL_CALLBACKS, "stack", ()):
            raise RuntimeError(self._tool_reentry_message())

    def _enter_contextvar_tool_callback(self) -> contextvars.Token[frozenset[int]]:
        gates = _TOOL_CALLBACK_GATES.get()
        return _TOOL_CALLBACK_GATES.set(gates | {id(self)})

    def _tool_reentry_message(self) -> str:
        return (
            f"Cannot call execute/aexecute on the same {self._interpreter_name} "
            "from a host tool callback"
        )


class SandboxBackend(str, Enum):
    """Named sandbox backends supported by PredictRLM."""

    JSPI = "jspi"
    SBX = "sbx"


class SbxConfig(BaseModel):
    """Configuration for the native-CPython sandbox backends.

    The ``runtime`` field selects how the JSON-RPC ``python_runner`` is
    launched. All three runtimes share the same filesystem-staged file model
    and stdio JSON-RPC protocol; they differ only in process isolation:

    - ``"sbx"``  — Docker Sandboxes via the ``sbx`` CLI (the original backend).
    - ``"docker"`` — a plain ``docker run -i`` container. Requires the ``docker``
      CLI + daemon and a prebaked ``image`` carrying the runner's packages.
    - ``"host"`` — a bare ``python3`` subprocess on the host (no isolation).
      Useful for low-latency/debug runs where the host venv already has the
      needed packages.
    """

    name: str | None = None
    cpus: int | None = None
    memory: str | None = None
    template: str | None = DEFAULT_SBX_TEMPLATE
    kit: str | None = None
    branch: str | None = None
    persist: bool = False
    remove_on_shutdown: bool = True
    extra_workspaces: list[str] = Field(default_factory=list)
    workspace_read_only: bool = False
    create_timeout: float = 120.0
    exec_timeout: float = 300.0
    shutdown_timeout: float = 2.0

    # Wall-clock budget for a single host-side tool call, mirroring the JSPI
    # backend's ``TOOL_CALL_TIMEOUT_SEC``. Exceeding it returns a RECOVERABLE
    # error to the sandbox ("tool ... timed out after Ns") instead of letting
    # ``exec_timeout`` expire, which kills the runner process and turns a slow
    # tool into an unrecoverable ``SandboxFatalError``. Must stay below
    # ``exec_timeout`` so the tool guard fires first; values that don't are
    # clamped (see ``SbxInterpreter._tool_call_budget``).
    tool_call_timeout: float = 180.0

    # Runtime selection and plain-Docker / host-subprocess options. Defaults
    # preserve the original ``sbx`` CLI behavior.
    runtime: Literal["sbx", "docker", "host"] = "sbx"
    image: str | None = None
    docker_network: str = "none"
    docker_extra_args: list[str] = Field(default_factory=list)
    python_executable: str = "python3"
    staging_root_base: str | None = None
    host_sandbox_root: str | None = Field(
        default=None,
        description=(
            "Host-runtime only. Must resolve to the literal /sandbox directory. "
            "This lets unpatched stdlib calls to literal /sandbox/... paths and "
            "predict-RLM's staged file sync converge on the same real directory."
        ),
    )


class PredictRLMInterpreter(Protocol):
    """Runtime methods PredictRLM needs from a sandbox interpreter."""

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any: ...

    async def aexecute(
        self, code: str, variables: dict[str, Any] | None = None
    ) -> Any: ...

    def mount_file_at(self, host_path: str, virtual_path: str) -> None: ...

    def mkdir_p(self, virtual_path: str) -> None: ...

    def list_dir(self, virtual_path: str) -> list[str]: ...

    def sync_file_to(self, virtual_path: str, host_path: str) -> None: ...

    def shutdown(self) -> None: ...
