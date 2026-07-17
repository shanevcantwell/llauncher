"""Unit tests for the systemd installer's legacy-key migration + dedupe.

Issue #285 (installer half of the "403s keep coming back" recurrence,
paired with #293's runtime half): the pre-#139 migration
(``LAUNCHER_AGENT_*`` → ``LLAUNCHER_AGENT_*``) rewrote every legacy line's
prefix with no dedupe, so a legacy line whose migrated key already existed
as a canonical line became a DUPLICATE. For ``LLAUNCHER_AGENT_TOKEN`` that
duplicate is a split-brain footgun. The fix DROPS a legacy line whose
migrated key already exists (canonical line wins), loudly.

The dedupe logic is extracted to ``scripts/systemd/migrate_env_keys.sh`` so
it is testable in isolation — these tests source that file and call
``migrate_and_dedupe_env_keys`` directly, without driving the full
installer's venv/systemctl preflight. Skipped if ``bash`` is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash not available"
)


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError(f"Could not locate repo root from {here}")


def _run_migration(env_file: Path) -> subprocess.CompletedProcess[str]:
    """Source the helper and run the migration over ``env_file``."""
    helper = _repo_root() / "scripts" / "systemd" / "migrate_env_keys.sh"
    script = (
        f'set -euo pipefail\n'
        f'source "{helper}"\n'
        f'migrate_and_dedupe_env_keys "{env_file}"\n'
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )


def test_legacy_token_colliding_with_canonical_is_dropped(tmp_path: Path) -> None:
    """A legacy token line whose migrated key already exists is dropped,
    leaving exactly one canonical token line (the pre-existing value)."""
    env = tmp_path / "agent.env"
    env.write_text(
        "LLAUNCHER_AGENT_TOKEN=good\n"
        "# comment line\n"
        "LAUNCHER_AGENT_TOKEN=legacy-bad\n"
        "LAUNCHER_AGENT_HOST=1.2.3.4\n"
        "LLAUNCHER_AGENT_PORT=8765\n"
    )

    result = _run_migration(env)

    lines = env.read_text().splitlines()
    token_lines = [ln for ln in lines if ln.strip().startswith("LLAUNCHER_AGENT_TOKEN=")]
    assert token_lines == ["LLAUNCHER_AGENT_TOKEN=good"], token_lines
    # The colliding legacy line was DROPPED, not rewritten into a second line.
    assert "legacy-bad" not in env.read_text()
    # A non-colliding legacy key (HOST) is still migrated in place.
    assert "LLAUNCHER_AGENT_HOST=1.2.3.4" in lines
    assert "LAUNCHER_AGENT_HOST=1.2.3.4" not in lines
    # Loud message names the drop and cites the issue.
    assert "Dropped" in result.stdout or "Dropped" in result.stderr
    assert "#285" in (result.stdout + result.stderr)


def test_legacy_line_preceding_canonical_is_dropped(tmp_path: Path) -> None:
    """PR #325 review (regression pinned): the canonical line wins the
    collision even when a legacy line appears BEFORE it in file order. The
    canonical-key set must be seeded from the WHOLE file before any
    drop/migrate decision — a single top-to-bottom pass that grows the set
    as it reads would migrate the leading legacy line and leave two
    canonical lines."""
    env = tmp_path / "agent.env"
    env.write_text(
        "LAUNCHER_AGENT_TOKEN=legacy1\n"
        "LLAUNCHER_AGENT_TOKEN=canon\n"
        "LAUNCHER_AGENT_TOKEN=legacy2\n"
    )

    result = _run_migration(env)

    lines = env.read_text().splitlines()
    token_lines = [ln for ln in lines if ln.strip().startswith("LLAUNCHER_AGENT_TOKEN=")]
    # Exactly one canonical line survives — the pre-existing canonical value.
    assert token_lines == ["LLAUNCHER_AGENT_TOKEN=canon"], token_lines
    assert "legacy1" not in env.read_text()
    assert "legacy2" not in env.read_text()
    assert "Dropped" in (result.stdout + result.stderr)


def test_samepass_legacy_collision_with_no_canonical_line_is_deduped(
    tmp_path: Path,
) -> None:
    """Issue #298: two legacy same-key lines with NO pre-existing canonical
    line must still dedupe down to exactly one canonical line — the
    collision produced WITHIN this migration pass, not just against a
    pre-existing canonical line."""
    env = tmp_path / "agent.env"
    env.write_text(
        "LAUNCHER_AGENT_TOKEN=first\n"
        "LAUNCHER_AGENT_TOKEN=second\n"
        "LAUNCHER_AGENT_HOST=1.2.3.4\n"
    )

    result = _run_migration(env)

    lines = env.read_text().splitlines()
    token_lines = [ln for ln in lines if ln.strip().startswith("LLAUNCHER_AGENT_TOKEN=")]
    # Exactly one canonical line results; the first occurrence wins.
    assert token_lines == ["LLAUNCHER_AGENT_TOKEN=first"], token_lines
    assert "second" not in env.read_text()
    assert "LLAUNCHER_AGENT_HOST=1.2.3.4" in lines
    # The drop is reported loudly, same as the pre-existing-canonical case.
    assert "Dropped" in result.stdout or "Dropped" in result.stderr


def test_legacy_only_migrates_without_dropping(tmp_path: Path) -> None:
    """No canonical collision: legacy keys migrate in place, none dropped,
    exactly one canonical token line results."""
    env = tmp_path / "agent.env"
    env.write_text(
        "LAUNCHER_AGENT_TOKEN=only-legacy\n"
        "LAUNCHER_AGENT_HOST=1.2.3.4\n"
    )

    result = _run_migration(env)

    text = env.read_text()
    assert text.count("LLAUNCHER_AGENT_TOKEN=") == 1
    assert "LLAUNCHER_AGENT_TOKEN=only-legacy" in text
    assert "LLAUNCHER_AGENT_HOST=1.2.3.4" in text
    assert "Migrated" in (result.stdout + result.stderr)
    assert "Dropped" not in (result.stdout + result.stderr)


def test_no_legacy_keys_is_noop(tmp_path: Path) -> None:
    """A file with only canonical keys is left byte-for-byte unchanged."""
    env = tmp_path / "agent.env"
    original = "LLAUNCHER_AGENT_TOKEN=good\nLLAUNCHER_AGENT_HOST=9.9.9.9\n"
    env.write_text(original)

    _run_migration(env)

    assert env.read_text() == original


def test_comment_lines_are_never_migrated(tmp_path: Path) -> None:
    """A commented legacy line is not a key line and passes through."""
    env = tmp_path / "agent.env"
    env.write_text(
        "# LAUNCHER_AGENT_TOKEN=example\n"
        "LLAUNCHER_AGENT_TOKEN=real\n"
    )

    _run_migration(env)

    text = env.read_text()
    # The commented single-L legacy line is untouched (not migrated to LLAUNCHER).
    assert "# LAUNCHER_AGENT_TOKEN=example" in text
    # Exactly one canonical token line, and it is the pre-existing real one.
    real_lines = [
        ln for ln in text.splitlines()
        if ln.startswith("LLAUNCHER_AGENT_TOKEN=")
    ]
    assert real_lines == ["LLAUNCHER_AGENT_TOKEN=real"]


def test_file_permissions_preserved(tmp_path: Path) -> None:
    """The in-place rewrite preserves the env file's mode (0640 group-read
    matters for the systemd --system UI read path)."""
    import stat as stat_mod

    env = tmp_path / "agent.env"
    env.write_text(
        "LLAUNCHER_AGENT_TOKEN=good\nLAUNCHER_AGENT_TOKEN=legacy-bad\n"
    )
    env.chmod(0o640)

    _run_migration(env)

    mode = stat_mod.S_IMODE(env.stat().st_mode)
    assert mode == 0o640, f"expected 0640 preserved, got {oct(mode)}"
