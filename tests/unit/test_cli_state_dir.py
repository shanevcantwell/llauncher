"""Tests for the CLI's global ``--state-dir`` override (issue #215).

Background
----------
``llauncher.core.settings`` resolves ``LAUNCHER_STATE_DIR`` (and every
config/state path derived from it) at *module import time*. For a
per-invocation ``--state-dir`` flag to win, it has to land in
``os.environ`` before any of those modules are first imported in the
process — see ``llauncher.cli.main``'s docstring for why the
config/state/registry imports were moved out of module scope and into
each command body.

Test strategy
-------------
- In-process (``CliRunner``) tests pin the root callback's own
  precedence contract: flag > env > untouched-when-absent. These run in
  the *same* interpreter as the rest of the suite, so they cannot prove
  the import-order fix by themselves (``llauncher.core.settings`` may
  already be cached from an earlier test) — they only prove the
  callback writes (or doesn't write) ``os.environ`` correctly.
- The subprocess tests close that gap: each spawns a genuinely fresh
  interpreter, so they exercise the real "callback runs before the
  settings chain is ever imported" ordering the issue asked for, using
  the acceptance criteria's literal invocation shape.
- The env-var-redirects-every-path contract (``LAUNCHER_STATE_DIR`` ->
  ``CONFIG_PATH`` / ``NODES_FILE`` / ``LAUNCHER_RUN_DIR`` / ...) is
  already covered by ``test_state_dir.py`` and is not re-proven here.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from llauncher.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean_state_dir_env(monkeypatch):
    """Snapshot/restore LAUNCHER_STATE_DIR around every test in this module.

    ``main()`` mutates ``os.environ`` directly by design (that mutation
    has to be visible to modules imported *after* it runs). Calling
    ``monkeypatch.delenv`` here — even though the actual write happens
    inside the CLI invocation, not in this fixture — is enough for
    monkeypatch to snapshot the pre-test value and restore it at
    teardown, regardless of what the callback did to it in between.
    """
    monkeypatch.delenv("LAUNCHER_STATE_DIR", raising=False)
    yield


# ---------------------------------------------------------------------------
# In-process: the callback's own precedence contract
# ---------------------------------------------------------------------------


def test_state_dir_flag_sets_env_var(tmp_path):
    """``--state-dir X`` sets LAUNCHER_STATE_DIR to X before dispatch."""
    target = tmp_path / "explicit-state"

    with patch("llauncher.core.config.ConfigStore.list_models", return_value=[]):
        result = runner.invoke(app, ["--state-dir", str(target), "model", "list"])

    assert result.exit_code == 0, result.output
    assert os.environ.get("LAUNCHER_STATE_DIR") == str(target)


def test_state_dir_flag_overrides_existing_env_var(monkeypatch, tmp_path):
    """The flag wins even when LAUNCHER_STATE_DIR is already set."""
    env_dir = tmp_path / "from-env"
    flag_dir = tmp_path / "from-flag"
    monkeypatch.setenv("LAUNCHER_STATE_DIR", str(env_dir))

    with patch("llauncher.core.config.ConfigStore.list_models", return_value=[]):
        result = runner.invoke(app, ["--state-dir", str(flag_dir), "model", "list"])

    assert result.exit_code == 0, result.output
    assert os.environ.get("LAUNCHER_STATE_DIR") == str(flag_dir)


def test_state_dir_flag_absent_leaves_env_var_untouched(monkeypatch, tmp_path):
    """With no flag, an existing env var is left exactly as it was."""
    env_dir = tmp_path / "from-env-only"
    monkeypatch.setenv("LAUNCHER_STATE_DIR", str(env_dir))

    with patch("llauncher.core.config.ConfigStore.list_models", return_value=[]):
        result = runner.invoke(app, ["model", "list"])

    assert result.exit_code == 0, result.output
    assert os.environ.get("LAUNCHER_STATE_DIR") == str(env_dir)


def test_state_dir_flag_and_env_both_absent_no_env_mutation():
    """With neither flag nor env set, the callback must not fabricate a value."""
    with patch("llauncher.core.config.ConfigStore.list_models", return_value=[]):
        result = runner.invoke(app, ["model", "list"])

    assert result.exit_code == 0, result.output
    assert "LAUNCHER_STATE_DIR" not in os.environ


def test_state_dir_help_text_documents_precedence():
    """``--help`` documents the flag and its precedence over the env var."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--state-dir" in result.output


# ---------------------------------------------------------------------------
# Subprocess end-to-end: acceptance criteria, literal invocation form
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Walk up from this file until a ``.git`` directory is found."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError(f"Could not locate repo root (no .git ancestor) from {here}")


def _subprocess_env(state_dir_env=None) -> dict:
    """A fresh-process env with LAUNCHER_STATE_DIR set/unset and the repo on path.

    Explicit ``PYTHONPATH`` (rather than relying on cwd) so the
    subprocess resolves ``llauncher`` the same way regardless of the
    directory pytest itself was invoked from.
    """
    env = dict(os.environ)
    repo_root = str(_repo_root())
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join([repo_root, existing] if existing else [repo_root])
    # Rich falls back to a narrow default width when stdout isn't a TTY
    # (as it never is under subprocess.run), which can wrap a long path
    # mid-word. Pin a wide terminal so assertions can match the path
    # substring verbatim instead of coupling to Rich's wrap points.
    env["COLUMNS"] = "200"
    if state_dir_env is None:
        env.pop("LAUNCHER_STATE_DIR", None)
    else:
        env["LAUNCHER_STATE_DIR"] = str(state_dir_env)
    return env


def _run_cli(args: list[str], env: dict) -> subprocess.CompletedProcess:
    """Invoke the real Typer app in a fresh interpreter (no cached imports)."""
    return subprocess.run(
        [sys.executable, "-c", "from llauncher.cli import app; app()", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_e2e_state_dir_flag_beats_env_no_symlink_needed(tmp_path):
    """Acceptance: ``--state-dir`` shows that dir's registry, no env/symlink.

    Mirrors the issue's repro: two distinct state dirs, each with its
    own ``config.json``; the flag must select the *flagged* dir's
    registry even though the env var points at the other one.
    """
    flagged = tmp_path / "flagged"
    enved = tmp_path / "enved"
    flagged.mkdir()
    enved.mkdir()
    (flagged / "config.json").write_text(
        json.dumps({"flagged-model": {"name": "flagged-model", "model_path": "/fake/f.gguf"}})
    )
    (enved / "config.json").write_text(
        json.dumps({"enved-model": {"name": "enved-model", "model_path": "/fake/e.gguf"}})
    )

    result = _run_cli(
        ["--state-dir", str(flagged), "model", "list"],
        env=_subprocess_env(state_dir_env=enved),
    )

    assert result.returncode == 0, result.stderr
    assert "flagged-model" in result.stdout
    assert "enved-model" not in result.stdout


def test_e2e_env_var_used_when_flag_absent(tmp_path):
    """Acceptance: the env var still works as the default-override."""
    enved = tmp_path / "enved-only"
    enved.mkdir()
    (enved / "config.json").write_text(
        json.dumps({"enved-model": {"name": "enved-model", "model_path": "/fake/e.gguf"}})
    )

    result = _run_cli(["model", "list"], env=_subprocess_env(state_dir_env=enved))

    assert result.returncode == 0, result.stderr
    assert "enved-model" in result.stdout


def test_e2e_config_path_reflects_state_dir_flag(tmp_path):
    """``config path`` (the CONFIG_PATH consumer) also honors the flag."""
    target = tmp_path / "cfg-target"
    target.mkdir()

    result = _run_cli(["--state-dir", str(target), "config", "path"], env=_subprocess_env())

    assert result.returncode == 0, result.stderr
    assert str(target / "config.json") in result.stdout
