"""Regression guard against TEST_SUITE_SUMMARY drift (issue #124).

``scripts/summarize_tests.py`` is a manual-regen inventory generator whose
output is committed at ``docs/generated/TEST_SUITE_SUMMARY.md``. Nothing
otherwise prevents the committed copy from drifting out of sync with the
actual test surface between commits.

This test wires the drift check into the existing pytest gate (the repo has
no GitHub Actions workflow; pytest *is* the CI surface, see ``pytest.ini``):
it regenerates the summary against the checked-out tree and fails if the
committed file differs. The issue's own trade-off analysis favours a
CI-gated regen over gitignoring, because gitignoring "defeats the point of
committing it."

To fix a failure: ``python scripts/summarize_tests.py`` then commit the
regenerated ``docs/generated/TEST_SUITE_SUMMARY.md``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

# tests/regression/ -> repo root is two parents up.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "summarize_tests.py"
COMMITTED_SUMMARY = PROJECT_ROOT / "docs" / "generated" / "TEST_SUITE_SUMMARY.md"


def _load_summarize_module() -> ModuleType:
    """Load ``scripts/summarize_tests.py`` (no package) by file path."""
    spec = importlib.util.spec_from_file_location("summarize_tests", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_test_suite_summary_is_not_stale(tmp_path):
    """The committed summary matches a fresh regeneration of the tree."""
    module = _load_summarize_module()

    regenerated_path = tmp_path / "TEST_SUITE_SUMMARY.md"
    # ``summarize_tests`` joins ``output_file`` onto the project root; an
    # absolute path short-circuits that join (pathlib drops the left side),
    # so the real committed file is never touched by this test.
    module.summarize_tests(module.TEST_DIRECTORY, str(regenerated_path))

    regenerated = regenerated_path.read_text(encoding="utf-8")
    committed = COMMITTED_SUMMARY.read_text(encoding="utf-8")

    assert regenerated == committed, (
        "docs/generated/TEST_SUITE_SUMMARY.md is out of sync with the test "
        "surface. Regenerate it with `python scripts/summarize_tests.py` and "
        "commit the result (issue #124)."
    )
