"""Shared hermetic, GPU-free fixtures for the issue #181 repro set.

THESE MODULES ENCODE *CURRENT BUGGY BEHAVIOR*. They PASS while the bug is
present (RED-pinning the status quo). When a defect is fixed, the matching
assertion here should be inverted or the module removed — see this package's
README.md and issue #181.

Safety design (a prior repro run perturbed live state — do not repeat):

- We NEVER touch real ports 8081/8082, the real llama.cpp binary, the GPU,
  or the running llauncher agent (pid 35859). Fakes bind nothing — they
  only carry argv the llauncher scanner matches on. We use a throwaway high
  port (``FAKE_PORT`` = 18181) that no real service uses.
- ``LAUNCHER_RUN_DIR`` / ``LAUNCHER_AUDIT_PATH`` / ``LAUNCHER_LOG_DIR`` are
  redirected to a per-process temp dir **before** llauncher is imported, so
  the operations layer reads our throwaway lockfiles, never the real ones in
  ``~/.llauncher/run``.
- Every spawned fake is tracked and torn down: live fakes are terminated and
  reaped; the deliberately-created zombie is reaped via ``waitpid`` in the
  same parent that forked it, so nothing leaks. ``assert_no_fake_leaks`` is
  the belt-and-suspenders check.

A "fake llama-server" is any process whose argv makes the llauncher scanner
match it. The scanner (``core/process.py``) matches via
``"llama-server" in proc.name() or any("llama-server" in c for c in cmdline)``
and reads ``--port N`` / ``-m PATH`` from argv. We get a match by setting
argv[0] to ``"llama-server"`` (the kernel ``comm``/``name`` stays the real
executable's, which is fine — the cmdline substring carries the match).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

# Throwaway high port — never a real service. Do NOT change to 8081/8082.
FAKE_PORT = 18181
FAKE_MODEL_PATH = "/fake/issue181/model.gguf"

# A second shared-path config (Defect B) points at the SAME gguf as the
# launched identity, mirroring the live collision
# (Qwen3.5-27B-UD-Q6_K_XL vs …-nommproj → one .gguf).
SHARED_MODEL_PATH = "/fake/issue181/shared-collision.gguf"

# argv[0] the scanner matches on. The real binary under the hood is the
# venv python (so the fake is a cheap, well-behaved long-lived sleeper that
# tolerates arbitrary trailing argv — unlike coreutils ``sleep`` which
# rejects unknown flags).
_FAKE_ARGV0 = "llama-server"
_FAKE_SLEEPER = "import time; time.sleep(3600)"


def make_temp_env() -> Path:
    """Create a temp run/audit/log dir and export the llauncher env vars.

    MUST be called *before* importing any llauncher module, because
    ``llauncher.core.settings`` snapshots these env vars into module-level
    constants at import time, and ``operations.stop`` resolves the lockfile
    dir from that constant.

    Returns the temp directory (the caller owns cleanup, but the OS temp
    reaper handles it too; we never write outside it).
    """
    tmp = Path(tempfile.mkdtemp(prefix="repro_issue181_"))
    run_dir = tmp / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    os.environ["LAUNCHER_RUN_DIR"] = str(run_dir)
    os.environ["LAUNCHER_AUDIT_PATH"] = str(tmp / "audit.jsonl")
    os.environ["LAUNCHER_LOG_DIR"] = str(tmp / "logs")
    return tmp


class FakeServer:
    """A live fake llama-server: argv-matchable, binds nothing, long-lived."""

    def __init__(self, port: int, model_path: str):
        self.port = port
        self.model_path = model_path
        argv = [
            _FAKE_ARGV0,
            "-c",
            _FAKE_SLEEPER,
            "-m",
            model_path,
            "--port",
            str(port),
        ]
        self._proc = subprocess.Popen(argv, executable=sys.executable)
        self.pid = self._proc.pid
        # Give the kernel a beat to publish argv so the scanner sees it.
        _wait_until_scannable(self.pid)

    def terminate_and_reap(self) -> None:
        """Kill and reap — no leak."""
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
                self._proc.wait(timeout=5)
            except Exception:
                pass


def _wait_until_scannable(pid: int, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            cl = psutil.Process(pid).cmdline()
            if any("llama-server" in c for c in cl):
                return
        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
            pass
        time.sleep(0.02)


class ZombieFake:
    """A fake llama-server forced into <defunct>/zombie state, unreaped.

    Fork a child that becomes the fake, SIGKILL it, and DO NOT waitpid it in
    the forking parent — leaving a zombie whose pid still exists but whose
    ``cmdline()`` raises ``psutil.ZombieProcess``. ``reap()`` (called in
    teardown, same parent) clears it so nothing leaks.
    """

    def __init__(self, port: int, model_path: str):
        self.port = port
        self._reaped = False
        pid = os.fork()
        if pid == 0:  # pragma: no cover — child path replaced by exec
            try:
                os.execvp(
                    "/bin/bash",
                    [
                        "bash",
                        "-c",
                        f"exec -a {_FAKE_ARGV0} sleep 3600 -m {model_path} "
                        f"--port {port}",
                    ],
                )
            finally:
                os._exit(127)
        self.pid = pid
        # Let the exec land, then kill -> zombie (parent has not reaped).
        _wait_until_status(pid, want_running=True)
        os.kill(pid, signal.SIGKILL)
        _wait_until_status(pid, want_running=False)  # becomes zombie

    def is_zombie(self) -> bool:
        try:
            return psutil.Process(self.pid).status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    def reap(self) -> None:
        if self._reaped:
            return
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass
        self._reaped = True


def _wait_until_status(pid: int, *, want_running: bool, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            st = psutil.Process(pid).status()
            running = st not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
            if running == want_running:
                return
        except psutil.NoSuchProcess:
            if not want_running:
                return
        time.sleep(0.02)


def assert_no_fake_leaks() -> None:
    """Fail loudly if any fake (our argv / FAKE_PORT) survives teardown.

    Excludes shell-snapshot wrappers whose *command text* merely echoes our
    heredoc/argv strings — those are not fakes, just noise in ``pgrep -af``.
    """
    leaks = []
    for proc in psutil.process_iter(["pid", "cmdline", "status"]):
        try:
            cl = proc.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except psutil.ZombieProcess:
            # An unreaped zombie of ours would also be a leak; detect via name.
            cl = []
        if not cl:
            continue
        joined = " ".join(cl)
        if "shell-snapshots" in joined or "pgrep" in joined:
            continue
        # A real fake of ours: argv[0] exactly "llama-server" AND our port/model.
        if cl[0].endswith("llama-server") and (
            f"--port {FAKE_PORT}".split()[-1] in cl
            or FAKE_MODEL_PATH in cl
            or SHARED_MODEL_PATH in cl
        ):
            leaks.append((proc.pid, joined))
    assert not leaks, f"LEAKED fake processes: {leaks}"


def make_model_config(name: str, model_path: str):
    """Construct a real ModelConfig without on-disk path validation."""
    from llauncher.models.config import ModelConfig

    return ModelConfig.from_dict_unvalidated({"name": name, "model_path": model_path})


def fresh_state_with_models(models: dict):
    """Build a real ``LauncherState`` carrying ``models`` WITHOUT reading the
    real ``~/.llauncher/config.json``.

    ``LauncherState.__post_init__`` calls ``refresh()`` which calls
    ``ConfigStore.load()`` (a hardcoded ``~/.llauncher`` path, NOT
    env-configurable). We monkeypatch ``ConfigStore.load`` to return our
    hermetic models so the repro never touches the operator's real config.
    """
    from llauncher import state as state_mod
    from llauncher.core import config as config_mod

    orig_load = config_mod.ConfigStore.load
    config_mod.ConfigStore.load = staticmethod(lambda: dict(models))
    try:
        st = state_mod.LauncherState()
    finally:
        config_mod.ConfigStore.load = orig_load
    # refresh_running_servers already ran inside __post_init__; the models
    # dict is now ours. Return for the caller to drive.
    st.models = dict(models)
    return st
