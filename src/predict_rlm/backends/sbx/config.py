"""Docker Sandboxes backend configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

DEFAULT_SBX_TEMPLATE = "docker.io/docker/sandbox-templates:shell"


class SbxConfig(BaseModel):
    """Configuration for the Docker Sandboxes backend.

    The ``runtime`` field selects how the JSON-RPC supervisor is launched. All
    three runtimes share the same filesystem-staged file model and stdio
    JSON-RPC protocol; they differ only in process isolation:

    - ``"sbx"``  — Docker Sandboxes via the ``sbx`` CLI (the original backend).
    - ``"docker"`` — a plain ``docker run -i`` container. Requires the ``docker``
      CLI + daemon and a prebaked ``image`` carrying the supervisor's packages.
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
    reuse: bool = False
    stop_on_shutdown: bool = False
    extra_workspaces: list[str] = Field(default_factory=list)
    workspace_read_only: bool = False
    create_timeout: float = 120.0
    exec_timeout: float = 300.0
    websocket_port: int = 0
    websocket_startup_timeout: float = 30.0
    websocket_max_message_bytes: int = 32 * 1024 * 1024

    # Runtime selection and plain-Docker / host-subprocess options. Defaults
    # preserve the original ``sbx`` CLI behavior. The docker/host runtimes use
    # the stdio JSON-RPC transport (no websocket supervisor).
    runtime: Literal["sbx", "docker", "host"] = "sbx"
    image: str | None = None
    docker_network: str = "none"
    docker_extra_args: list[str] = Field(default_factory=list)
    python_executable: str = "python3"
    # Lets the caller place the bind-mounted staging dir on a Docker-shareable
    # path (e.g. macOS Docker Desktop only shares specific host dirs); falls
    # back to the process cwd.
    staging_root_base: str | None = None
    host_sandbox_root: str | None = Field(
        default=None,
        description=(
            "Host-runtime only. Must resolve to the literal /sandbox directory. "
            "This lets unpatched stdlib calls to literal /sandbox/... paths and "
            "predict-RLM's staged file sync converge on the same real directory."
        ),
    )

    @model_validator(mode="after")
    def _apply_reuse_semantics(self) -> "SbxConfig":
        if self.reuse:
            if not self.name:
                raise ValueError("SbxConfig.reuse=True requires a non-empty `name`.")
            self.persist = True
            self.remove_on_shutdown = False
        return self
