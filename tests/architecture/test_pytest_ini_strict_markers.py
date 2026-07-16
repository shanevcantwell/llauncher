"""Static + behavioral guard: pytest.ini is the sole marker inventory (#318).

Why this test exists
---------------------
``integration_real`` and ``real_model_health`` were previously registered
only *dynamically*, via ``pytest_configure`` hooks in
``tests/integration/conftest.py`` and ``tests/conftest.py`` — "so coverage
runs do not error on unknown markers." That workaround meant ``pytest.ini``
was not the authoritative marker inventory a reader assumes, and
``--strict-markers`` was never set, so a typo'd or forgotten marker would
only warn instead of failing collection.

This test pins the fix: both custom markers are declared directly in
``pytest.ini``'s ``markers=`` block, ``--strict-markers`` is in ``addopts``,
and (behaviorally) an undeclared marker actually fails collection under the
repo's own ``pytest.ini``.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

# Repo root = two parents up from this file: tests/architecture/<file>.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYTEST_INI = _REPO_ROOT / "pytest.ini"

_REQUIRED_MARKERS = ("integration", "live", "integration_real", "real_model_health")


def test_pytest_ini_has_strict_markers_addopt() -> None:
    """``--strict-markers`` must be present in ``addopts``."""
    text = _PYTEST_INI.read_text()
    addopts_lines = [
        line for line in text.splitlines() if line.strip().startswith("addopts")
    ]
    assert addopts_lines, "pytest.ini has no addopts line"
    assert "--strict-markers" in addopts_lines[0], (
        "pytest.ini addopts is missing --strict-markers: "
        f"{addopts_lines[0]!r}"
    )


def test_pytest_ini_declares_every_marker_used_by_the_suite() -> None:
    """Every marker referenced anywhere in ``tests/`` must be declared here.

    Guards against the exact gap #318 closed: ``integration_real`` and
    ``real_model_health`` were used by the suite but only ever registered
    dynamically, never in ``pytest.ini`` itself.
    """
    text = _PYTEST_INI.read_text()
    for marker in _REQUIRED_MARKERS:
        assert f"{marker}:" in text, (
            f"pytest.ini markers= block is missing a declaration for "
            f"{marker!r}"
        )


def test_conftests_no_longer_dynamically_register_markers() -> None:
    """The dynamic ``pytest_configure`` marker-registration workaround is gone.

    ``pytest.ini`` is now the single source of truth (per the issue); a
    ``pytest_configure`` hook re-appearing in either conftest would silently
    reintroduce a second inventory that ``--strict-markers`` can't audit by
    reading ``pytest.ini`` alone.
    """
    for rel in ("tests/conftest.py", "tests/integration/conftest.py"):
        text = (_REPO_ROOT / rel).read_text()
        assert "def pytest_configure(" not in text, (
            f"{rel} re-registers markers dynamically; pytest.ini should be "
            "the sole marker inventory (#318)"
        )


def test_undeclared_marker_fails_collection_under_strict_markers() -> None:
    """Behavioral proof: an unregistered marker errors out, not just warns.

    Runs a throwaway test file, using the *real* ``pytest.ini``, that applies
    a marker never declared anywhere. Under ``--strict-markers`` collection
    must fail; without it, this would merely emit a warning and pass.
    """
    scratch = _REPO_ROOT / "tests" / "architecture" / "_strict_markers_probe.py"
    scratch.write_text(
        textwrap.dedent(
            """\
            import pytest

            @pytest.mark.definitely_not_a_registered_marker
            def test_probe():
                assert True
            """
        )
    )
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--no-cov", str(scratch)],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, (
            "an undeclared marker did not fail collection under "
            f"--strict-markers: stdout={result.stdout!r}"
        )
        assert "not found in `markers` configuration option" in result.stdout, (
            f"unexpected failure mode: stdout={result.stdout!r}"
        )
    finally:
        scratch.unlink()
