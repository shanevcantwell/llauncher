"""Unit tests for ``llauncher.core.log_rotation`` (ADR-LLNCH-013).

The rotation helper is opportunistic — it runs at process-start time,
not on every write — and tests exercise it directly against tmp_path
without touching subprocess machinery.
"""

from __future__ import annotations

from pathlib import Path

from llauncher.core.log_rotation import rotate_if_needed


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------


def test_no_op_when_file_absent(tmp_path: Path) -> None:
    """No file → nothing to rotate; returns False without error."""
    target = tmp_path / "missing.log"

    rotated = rotate_if_needed(target, max_bytes=1, keep=3)

    assert rotated is False
    assert not target.exists()


def test_no_op_when_under_threshold(tmp_path: Path) -> None:
    """File smaller than ``max_bytes`` is left alone."""
    target = tmp_path / "small.log"
    target.write_text("just a few bytes")

    rotated = rotate_if_needed(target, max_bytes=10_000, keep=3)

    assert rotated is False
    assert target.exists()
    assert target.read_text() == "just a few bytes"


def test_no_op_when_max_bytes_zero_or_negative(tmp_path: Path) -> None:
    """``max_bytes <= 0`` disables rotation entirely (escape hatch for tests / opt-out)."""
    target = tmp_path / "any.log"
    target.write_text("X" * 1000)

    assert rotate_if_needed(target, max_bytes=0, keep=3) is False
    assert rotate_if_needed(target, max_bytes=-1, keep=3) is False
    # File remains untouched in both cases.
    assert target.read_text() == "X" * 1000


# ---------------------------------------------------------------------------
# Rotation behavior
# ---------------------------------------------------------------------------


def test_rotates_to_dot_one_when_over_threshold(tmp_path: Path) -> None:
    """Over-cap file moves to ``.log.1``; the live path is gone after rotation."""
    target = tmp_path / "big.log"
    target.write_text("Y" * 200)

    rotated = rotate_if_needed(target, max_bytes=100, keep=3)

    assert rotated is True
    assert not target.exists(), "live log should be moved out of the way"
    rotated_path = tmp_path / "big.log.1"
    assert rotated_path.exists()
    assert rotated_path.read_text() == "Y" * 200


def test_shifts_existing_rotated_files_up(tmp_path: Path) -> None:
    """``.log.1 → .log.2``, ``.log.2 → .log.3`` etc., on a fresh rotation."""
    target = tmp_path / "rolling.log"
    target.write_text("now-current")
    (tmp_path / "rolling.log.1").write_text("was-1")
    (tmp_path / "rolling.log.2").write_text("was-2")

    rotated = rotate_if_needed(target, max_bytes=1, keep=3)

    assert rotated is True
    # Slot 1 holds the formerly-live file.
    assert (tmp_path / "rolling.log.1").read_text() == "now-current"
    # Slot 2 is what was in slot 1.
    assert (tmp_path / "rolling.log.2").read_text() == "was-1"
    # Slot 3 is what was in slot 2.
    assert (tmp_path / "rolling.log.3").read_text() == "was-2"


def test_drops_oldest_when_over_keep(tmp_path: Path) -> None:
    """The file already at ``.log.{keep}`` is unlinked, not promoted off the end."""
    target = tmp_path / "rolling.log"
    target.write_text("now-current")
    (tmp_path / "rolling.log.1").write_text("was-1")
    (tmp_path / "rolling.log.2").write_text("was-2")
    (tmp_path / "rolling.log.3").write_text("oldest")  # at keep=3, this is the oldest kept

    rotated = rotate_if_needed(target, max_bytes=1, keep=3)

    assert rotated is True
    assert (tmp_path / "rolling.log.1").read_text() == "now-current"
    assert (tmp_path / "rolling.log.2").read_text() == "was-1"
    assert (tmp_path / "rolling.log.3").read_text() == "was-2"
    # Anything at slot keep+1 must NOT exist.
    assert not (tmp_path / "rolling.log.4").exists()
    # The previously-oldest content is gone.
    for n in range(1, 5):
        assert (tmp_path / f"rolling.log.{n}").read_text() != "oldest" if (
            tmp_path / f"rolling.log.{n}"
        ).exists() else True


def test_keep_zero_removes_live_file_outright(tmp_path: Path) -> None:
    """``keep=0`` semantics: rotate but retain no history."""
    target = tmp_path / "ephemeral.log"
    target.write_text("Z" * 50)

    rotated = rotate_if_needed(target, max_bytes=10, keep=0)

    assert rotated is True
    assert not target.exists()
    # No .log.1 was created.
    assert not (tmp_path / "ephemeral.log.1").exists()


def test_negative_keep_clamped_to_zero(tmp_path: Path) -> None:
    """``keep < 0`` behaves identically to ``keep=0``; no history retained."""
    target = tmp_path / "ephemeral.log"
    target.write_text("Z" * 50)

    rotated = rotate_if_needed(target, max_bytes=10, keep=-5)

    assert rotated is True
    assert not target.exists()


def test_size_exactly_at_threshold_does_not_rotate(tmp_path: Path) -> None:
    """``size <= max_bytes`` is the no-op condition; equality is *not* over."""
    target = tmp_path / "edge.log"
    target.write_text("A" * 100)

    rotated = rotate_if_needed(target, max_bytes=100, keep=3)

    assert rotated is False
    assert target.read_text() == "A" * 100


def test_aborts_on_partial_rename_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """A mid-chain ``os.replace`` failure must NOT leave the live file moved.

    The naive "best-effort, log-and-continue" loop would partially commit
    the chain shift and then move the live file into slot 1 anyway,
    leaving a permanent gap. The bail-on-first-failure behavior instead
    leaves the live file in place so the next start can retry cleanly.
    """
    import llauncher.core.log_rotation as mod

    target = tmp_path / "rolling.log"
    target.write_text("CURRENT")
    (tmp_path / "rolling.log.1").write_text("was-1")
    (tmp_path / "rolling.log.2").write_text("was-2")

    real_replace = mod.os.replace

    def selective_replace(src, dst):
        # Fail specifically on the .log.1 → .log.2 rename, after .log.2 →
        # .log.3 has already succeeded (worst-case partial-commit window).
        if str(src).endswith(".log.1") and str(dst).endswith(".log.2"):
            raise PermissionError("simulated mid-chain rename failure")
        return real_replace(src, dst)

    monkeypatch.setattr(mod.os, "replace", selective_replace)

    rotated = rotate_if_needed(target, max_bytes=1, keep=3)

    assert rotated is False, "rotation should have aborted, not partially committed"
    # The live file is untouched.
    assert target.exists()
    assert target.read_text() == "CURRENT"
    # The .log.1 file the failure happened on is also untouched.
    assert (tmp_path / "rolling.log.1").read_text() == "was-1"
