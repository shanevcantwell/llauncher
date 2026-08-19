"""Regression tests for issue #425 — two version mints disagreeing.

``pyproject.toml``'s ``[project].version`` is the sole authority for the
release version (ONE-MINT, ``docs/ARCHITECTURE.md`` rule 4). Before this fix,
``llauncher/__init__.py`` re-declared the version as a hardcoded literal that
had already drifted from the pyproject-minted value (0.4.0a0 vs 0.4.1a0), and
all three wire-facing consumers (``__main__.py`` ``--version``,
``agent/routing.py``'s ``/health``, ``agent/server.py``'s FastAPI app) read
that stale literal.

This file pins two invariants:

1. ``llauncher.__version__`` is *derived* from installed package metadata
   (``importlib.metadata.version("llauncher")``), matching the acceptance
   criterion verbatim, so it can never re-drift from the pyproject mint.
2. No second version literal exists anywhere under ``llauncher/`` — the
   enforcement surface for "the next hardcoded literal". This is a
   source-level grep pin (same pattern as
   ``tests/regression/test_html_escape_regression.py`` for control C11),
   since there is no CI grep-gate yet.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import llauncher

# Anchor on the package directory rather than walking up from __file__ so the
# test still works if the regression suite is relocated.
PACKAGE_ROOT = Path(llauncher.__file__).resolve().parent

# Matches a PEP 440-ish version literal assignment, e.g.:
#   __version__ = "0.4.0a0"
#   version = "0.4.1a0"
_VERSION_LITERAL_RE = re.compile(
    r"""^\s*__?version__?\s*=\s*["']\d+\.\d+[^"']*["']""",
    re.MULTILINE,
)


class TestVersionSingleMint:
    """llauncher.__version__ must equal the installed-package metadata version."""

    def test_version_matches_installed_metadata_or_fallback(self):
        """llauncher.__version__ tracks importlib.metadata, per the issue's acceptance test.

        When the package is installed (editable or otherwise), the two must
        match exactly. When it is not installed (raw checkout on
        PYTHONPATH — the state of this dev worktree), llauncher.__version__
        falls back to a sentinel rather than silently re-declaring a second
        literal; PackageNotFoundError is the expected shape in that case.
        """
        try:
            metadata_version = version("llauncher")
        except PackageNotFoundError:
            assert llauncher.__version__ == "0.0.0+unknown"
        else:
            assert llauncher.__version__ == metadata_version


class TestNoSecondVersionLiteral:
    """No module under llauncher/ may re-declare a version string literal.

    This is the enforcement surface named in the issue: absent a CI
    grep-gate, this source scan is what stops the next hardcoded
    ``__version__ = "..."`` from silently re-introducing a second mint.
    """

    def test_no_hardcoded_version_literal_outside_init(self):
        # llauncher/__init__.py is exempt: it holds the *derivation*
        # (`version("llauncher")`) plus a documented PackageNotFoundError
        # fallback sentinel (`"0.0.0+unknown"`), which is not a re-declared
        # release version and is covered by test_version_matches_installed_
        # metadata_or_fallback above. Every other module must have zero
        # version-literal assignments.
        init_file = PACKAGE_ROOT / "__init__.py"
        offenders = []
        for py_file in PACKAGE_ROOT.rglob("*.py"):
            if py_file == init_file:
                continue
            text = py_file.read_text(encoding="utf-8")
            for match in _VERSION_LITERAL_RE.finditer(text):
                offenders.append(f"{py_file.relative_to(PACKAGE_ROOT)}: {match.group(0).strip()}")
        assert not offenders, (
            "Found hardcoded version literal(s) outside the single mint "
            f"(pyproject.toml): {offenders}"
        )

    def test_init_has_no_hardcoded_release_literal(self):
        """__init__.py itself must not re-declare a real release version.

        Only the documented fallback sentinel ("0.0.0+unknown") is allowed;
        anything else (e.g. reintroducing "0.4.0a0") is the exact regression
        this issue fixes.
        """
        init_file = PACKAGE_ROOT / "__init__.py"
        text = init_file.read_text(encoding="utf-8")
        matches = [m.group(0).strip() for m in _VERSION_LITERAL_RE.finditer(text)]
        allowed = {'__version__ = "0.0.0+unknown"'}
        offenders = [m for m in matches if m not in allowed]
        assert not offenders, (
            f"llauncher/__init__.py must derive __version__ from package "
            f"metadata, not hardcode it: {offenders}"
        )


class TestVersionConsumersShareTheMint:
    """The three consumer sites named in the issue import the same __version__."""

    def test_main_imports_shared_version(self):
        from llauncher.__main__ import __version__ as main_version

        assert main_version is llauncher.__version__

    def test_routing_health_reports_shared_version(self):
        import asyncio

        from llauncher.agent.routing import health_check

        result = asyncio.run(health_check())
        assert result["version"] == llauncher.__version__

    def test_server_app_reports_shared_version(self):
        from llauncher.agent import server

        assert server.__version__ is llauncher.__version__
