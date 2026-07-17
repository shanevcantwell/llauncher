"""Static guard: the extras shape ruled 2026-07-17 does not regrow.

Why this test exists
---------------------
A bare ``pip install -e .`` used to produce an incomplete install: streamlit
lived behind an opt-in ``[ui]`` extra, so only the sanctioned
``run.bat install`` (``pip install -e ".[ui]"``) actually worked. The
operator ratified collapsing the UI and CLI extras (2026-07-17): UI is not
optional in this ecosystem, so its dependencies moved into base ``[project]
dependencies``, and the ``cli`` extra was deleted outright as vestigial
(typer/rich were already base deps). The operator separately ruled that the
``test`` extra is **kept** — dev-only test tooling stays opt-in via
``pip install -e ".[test]"``.

This test pins that specific shape: streamlit lives in base ``dependencies``;
there is no ``ui`` or ``cli`` key anywhere in ``[project.optional-dependencies]``;
and ``test`` is the *only* key present, so neither half of the collapse can
silently regrow nor silently disappear.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

# Repo root = two parents up from this file: tests/architecture/<file>.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _load_pyproject() -> dict:
    with _PYPROJECT.open("rb") as f:
        return tomllib.load(f)


def test_streamlit_is_a_base_dependency() -> None:
    """Streamlit (formerly the ``[ui]`` extra) must be a base dependency."""
    data = _load_pyproject()
    deps = data["project"]["dependencies"]
    assert any(dep.split(">=")[0].split("==")[0].strip() == "streamlit" for dep in deps), (
        "streamlit is missing from base [project] dependencies; a bare "
        "`pip install -e .` must pull it directly (no extras)"
    )


def test_no_ui_or_cli_extras() -> None:
    """``ui`` and ``cli`` must not exist as optional-dependencies keys."""
    data = _load_pyproject()
    extras = data["project"].get("optional-dependencies", {})
    assert "ui" not in extras, (
        "pyproject.toml [project.optional-dependencies] has a `ui` key "
        "again; UI deps were deliberately collapsed into base dependencies "
        "(2026-07-17 ratification) so `pip install -e .` is correct by "
        "construction"
    )
    assert "cli" not in extras, (
        "pyproject.toml [project.optional-dependencies] has a `cli` key "
        "again; typer/rich are base dependencies and the extra was vestigial"
    )


def test_test_extra_is_the_only_optional_dependency() -> None:
    """``test`` is the operator-ruled sole survivor of the extras collapse."""
    data = _load_pyproject()
    extras = data["project"].get("optional-dependencies", {})
    assert set(extras.keys()) == {"test"}, (
        f"expected [project.optional-dependencies] to contain exactly "
        f"{{'test'}}, found {sorted(extras.keys())!r}; the operator ruled "
        "2026-07-17 that `test` is kept as the sole extra while `ui`/`cli` "
        "are collapsed into base dependencies"
    )
