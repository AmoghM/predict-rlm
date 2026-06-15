"""Tests for the SbxInterpreter ``docker`` and ``host`` runtimes.

These runtimes share the ``sbx`` backend's filesystem-staged file model and
stdio JSON-RPC protocol; they only change how the ``python_runner`` process is
launched. The unit tests assert the launch argv without touching Docker; the
host smoke test exercises the full runner round-trip with the test interpreter;
the Docker test is skipped unless a daemon is reachable.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from predict_rlm.interpreter import SandboxFatalError
from predict_rlm.interpreters import SbxConfig, SbxInterpreter


def _host_interpreter(tmp_path: Path) -> SbxInterpreter:
    # Use the running interpreter as ``python3`` so the smoke test does not
    # depend on a system python3 being present / matching.
    return SbxInterpreter(
        config=SbxConfig(runtime="host", python_executable=sys.executable),
        preinstall_packages=False,
        _staging_root=tmp_path / "staging",
    )


class TestRuntimeCommandBuilding:
    def test_host_command_argv_and_dirs(self, tmp_path: Path):
        itp = SbxInterpreter(
            config=SbxConfig(runtime="host", python_executable="/usr/bin/python3"),
            preinstall_packages=False,
            _staging_root=tmp_path / "staging",
        )
        cmd = itp._build_host_runner_command()
        assert cmd[0] == "/usr/bin/python3"
        assert cmd[1] == "-u"
        assert cmd[2].endswith("python_runner.py")
        # runner is staged and the sandbox workdir is pre-created
        assert Path(cmd[2]).is_file()
        assert (itp._staging_root / "sandbox").is_dir()

    def test_host_sandbox_root_must_be_literal_sandbox(self, tmp_path: Path):
        with pytest.raises(SandboxFatalError, match="literal /sandbox"):
            SbxInterpreter(
                config=SbxConfig(
                    runtime="host",
                    python_executable="/usr/bin/python3",
                    host_sandbox_root=str(tmp_path / "real-sandbox"),
                ),
                preinstall_packages=False,
                _staging_root=tmp_path / "staging",
            )

    def test_docker_command_argv(self, tmp_path: Path):
        itp = SbxInterpreter(
            config=SbxConfig(
                runtime="docker",
                image="example/img:latest",
                name="job-abc",
                docker_network="none",
                docker_extra_args=["--cpus", "2"],
            ),
            preinstall_packages=False,
            _staging_root=tmp_path / "staging",
        )
        cmd = itp._build_docker_runner_command()
        # Resolved (symlink-canonical) path is used on both sides of the bind
        # mount so it matches the resolved paths baked into injected code.
        staging = str(itp._staging_root.resolve())

        assert cmd[:5] == ["docker", "run", "-i", "--rm", "--name"]
        assert "job-abc" in cmd
        assert itp._container_name == "job-abc"
        # staging mounted at its own path (injected file vars) + sandbox dir
        # mounted at a real /sandbox (stdlib fs ops) + workdir + sandbox-root env
        assert f"{staging}:{staging}" in cmd
        assert f"{staging}/sandbox:/sandbox" in cmd
        assert "/sandbox" in cmd  # workdir
        assert f"PREDICT_RLM_SBX_ROOT={staging}" in cmd
        assert "--network" in cmd and "none" in cmd
        # escape-hatch args appear before the image
        assert "--cpus" in cmd and "2" in cmd
        img_idx = cmd.index("example/img:latest")
        assert cmd[img_idx + 1 : img_idx + 4] == [
            itp.config.python_executable,
            "-u",
            cmd[-1],
        ]
        assert cmd[-1].endswith("python_runner.py")

    def test_docker_requires_image(self, tmp_path: Path):
        itp = SbxInterpreter(
            config=SbxConfig(runtime="docker", image=None),
            preinstall_packages=False,
            _staging_root=tmp_path / "staging",
        )
        with pytest.raises(SandboxFatalError, match="image"):
            itp._build_docker_runner_command()

    def test_docker_requires_cli(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "predict_rlm.interpreters.sbx.shutil.which", lambda name: None
        )
        itp = SbxInterpreter(
            config=SbxConfig(runtime="docker", image="example/img:latest"),
            preinstall_packages=False,
            _staging_root=tmp_path / "staging",
        )
        with pytest.raises(SandboxFatalError, match="docker"):
            itp._build_docker_runner_command()

    def test_staging_root_base_is_honored(self, tmp_path: Path):
        base = tmp_path / "shareable"
        base.mkdir()
        itp = SbxInterpreter(
            config=SbxConfig(runtime="host", staging_root_base=str(base)),
            preinstall_packages=False,
        )
        try:
            assert str(itp._staging_root).startswith(str(base))
        finally:
            itp.shutdown()


class TestHostRuntimeSmoke:
    def test_executes_native_cpython(self, tmp_path: Path):
        itp = _host_interpreter(tmp_path)
        try:
            out = itp.execute("print('hello from', 1 + 1)")
            assert "hello from 2" in out
        finally:
            itp.shutdown()

    def test_file_round_trip_to_host_staging(self, tmp_path: Path):
        itp = _host_interpreter(tmp_path)
        try:
            itp.mkdir_p("/sandbox/output")
            itp.execute(
                "open('/sandbox/output/out.txt', 'w').write('native-cpython')"
            )
            host_file = itp._staging_root / "sandbox" / "output" / "out.txt"
            assert host_file.read_text() == "native-cpython"
        finally:
            itp.shutdown()

    @pytest.mark.skipif(
        os.environ.get("PREDICT_RLM_RUN_HOST_SANDBOX_TESTS") != "1"
        or not os.access("/sandbox", os.W_OK),
        reason="requires an opt-in writable /sandbox owned by this test run",
    )
    def test_external_sandbox_root_supports_literal_stdlib_paths(self, tmp_path: Path):
        itp = SbxInterpreter(
            config=SbxConfig(
                runtime="host",
                python_executable=sys.executable,
                host_sandbox_root="/sandbox",
            ),
            preinstall_packages=False,
            _staging_root=tmp_path / "staging",
        )
        out = tmp_path / "out.txt"
        archive = tmp_path / "archive.zip"

        try:
            itp.mkdir_p("/sandbox/output")
            itp.execute(
                "import os, tempfile, zipfile\n"
                "fd, tmp = tempfile.mkstemp()\n"
                "os.write(fd, b'native-cpython')\n"
                "os.close(fd)\n"
                "os.replace(tmp, '/sandbox/output/out.txt')\n"
                "with zipfile.ZipFile('/sandbox/output/archive.zip', 'w') as z:\n"
                "    z.writestr('entry.txt', 'ok')"
            )
            itp.sync_file_to("/sandbox/output/out.txt", str(out))
            itp.sync_file_to("/sandbox/output/archive.zip", str(archive))
        finally:
            itp.shutdown()

        assert out.read_text(encoding="utf-8") == "native-cpython"
        assert archive.is_file()


@pytest.mark.skipif(
    shutil.which("docker") is None or os.environ.get("PREDICT_RLM_SKIP_DOCKER") == "1",
    reason="docker CLI/daemon not available",
)
class TestDockerRuntimeIntegration:
    IMAGE = os.environ.get("PREDICT_RLM_TEST_DOCKER_IMAGE", "python:3.12-slim")

    def _interpreter(self, tmp_path: Path) -> SbxInterpreter:
        return SbxInterpreter(
            config=SbxConfig(runtime="docker", image=self.IMAGE, docker_network="none"),
            preinstall_packages=False,
            _staging_root=tmp_path / "staging",
        )

    def test_executes_in_container(self, tmp_path: Path):
        itp = self._interpreter(tmp_path)
        try:
            out = itp.execute("import sys; print('py', sys.version_info[0])")
            assert "py 3" in out
        finally:
            itp.shutdown()

    def test_file_round_trip_and_cleanup(self, tmp_path: Path):
        itp = self._interpreter(tmp_path)
        itp.mkdir_p("/sandbox/output")
        itp.execute("open('/sandbox/output/out.txt', 'w').write('from-container')")
        host_file = itp._staging_root / "sandbox" / "output" / "out.txt"
        assert host_file.read_text() == "from-container"
        name = itp._container_name
        itp.shutdown()
        # ``--rm`` + shutdown reap leaves no container behind
        listed = os.popen(
            f"docker ps -a --filter name={name} --format {{{{.Names}}}}"
        ).read().strip()
        assert listed == ""
