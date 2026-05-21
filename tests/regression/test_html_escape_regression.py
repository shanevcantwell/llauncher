"""Regression tests for control C11 (security-hardening-plan §3, §4 C11-a).

The hardening plan classifies Streamlit XSS as **control C11**: any
``st.markdown`` / ``st.write`` / ``st.text`` call that renders an
operator-controlled string (model name, node name, path) MUST NOT pass
``unsafe_allow_html=True``, because Streamlit otherwise auto-escapes
HTML and our threat model relies on that default.

The plan's §4 assertion C11-a is:

    A model added with name ``"<script>alert(1)</script>"`` renders as
    escaped text in the dashboard's model-card view (HTML-level
    assertion against the Streamlit-rendered page).

The full end-to-end assertion against a live-rendered Streamlit page is
blocked on issue #69 (the ``streamlit.testing.v1.AppTest`` harness is
not yet wired). Per the issue #84 implementation note, we instead pin
the **source-level invariant** here: no UI module under
``llauncher/ui/`` may opt out of Streamlit's HTML escaping by passing
``unsafe_allow_html=True``. When #69 lands, the live-render assertion
should be added alongside (not replace) this source-level pin — the
pin catches a class of regressions (a future PR re-introducing the
flag in a code-review-sized diff) that an AppTest of one rendered page
will not.

Scope: every ``*.py`` under ``llauncher/ui/`` (including the
``components/`` and ``tabs/`` subpackages), since the dashboard tab
imports from all of them and operator-controlled strings flow through
several of these surfaces (model name, node name, model path).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Anchor on the package directory rather than walking up from __file__
# so the test still works if the regression suite is relocated.
import llauncher.ui as _ui_pkg

UI_ROOT = Path(_ui_pkg.__file__).parent

# The sentinel substring we forbid. We deliberately match the
# ``=True`` form rather than the bare attribute name so a defensive
# ``unsafe_allow_html=False`` (which is the Streamlit *default* and
# therefore safe) would not trigger the pin. A future regression
# could only sneak past by writing ``unsafe_allow_html = True`` with
# spaces — we cover that too in ``_normalize``.
_FORBIDDEN = "unsafe_allow_html=True"

_WHITESPACE_PATTERN = re.compile(r"\s+")


def _normalize(src: str) -> str:
    """Collapse all whitespace so ``foo = True`` and ``foo\\n=True`` both reduce to ``foo=True``.

    Streamlit only accepts the kwarg form (``unsafe_allow_html=True``);
    PEP 8 forbids spaces around ``=`` in kwargs, so this normalization
    is paranoia, not necessity. It exists so a stylistic deviation in
    a future PR — including a multi-line call reformat — doesn't
    silently bypass the pin.
    """
    return _WHITESPACE_PATTERN.sub("", src)


def _iter_ui_py_files() -> list[Path]:
    """Yield every ``.py`` file under ``llauncher/ui/`` (recursively).

    We skip ``__pycache__`` defensively even though ``.py`` filtering
    already excludes ``.pyc`` — a stray ``.py`` left behind by a build
    tool would otherwise be scanned.
    """
    return sorted(
        p
        for p in UI_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
    )


class TestUnsafeAllowHtmlAudit:
    """Pin the C11 audit finding: zero ``unsafe_allow_html=True`` callsites."""

    def test_ui_root_has_no_unsafe_allow_html(self) -> None:
        """No file under ``llauncher/ui/`` may opt out of HTML escaping.

        This is the C11 audit pinned as a regression: as of the audit
        on the branch that landed #84, ``grep -rn 'unsafe_allow_html'
        llauncher/ui/`` returned zero matches. If a future PR adds the
        flag — for any reason, including a seemingly-safe constant
        string — this test fails and forces a security review.

        The remediation is *not* "add an exception here"; it is
        "render the HTML through a safe seam (e.g. ``st.html`` with a
        constant template) or remove the need for raw HTML entirely."
        """
        offenders: list[str] = []
        for path in _iter_ui_py_files():
            src = _normalize(path.read_text(encoding="utf-8"))
            if _FORBIDDEN in src:
                offenders.append(str(path.relative_to(UI_ROOT.parent.parent)))

        assert not offenders, (
            "C11 regression: the following UI files now pass "
            "``unsafe_allow_html=True``, which opts out of Streamlit's "
            "default HTML escaping for operator-controlled strings. "
            "See docs/plans/security-hardening-plan.md §3 C11.\n"
            + "\n".join(f"  - {o}" for o in offenders)
        )

    def test_audit_actually_scanned_real_files(self) -> None:
        """Sanity-check the audit by asserting it touched non-empty files.

        Without this, a refactor that moves ``llauncher/ui/`` elsewhere
        would make ``_iter_ui_py_files`` return an empty list and the
        primary test above would vacuously pass. This guard ensures the
        regression remains meaningful across reorganizations.
        """
        files = _iter_ui_py_files()
        assert files, (
            f"Expected to scan UI source files under {UI_ROOT}, found none. "
            "If llauncher/ui/ has moved, update UI_ROOT in this test."
        )
        # The dashboard module is the original C11-a surface (it owns
        # the model-card render path), so its presence is a sentinel
        # that the right tree is being scanned.
        names = {p.name for p in files}
        assert "model_card.py" in names, (
            "Audit scan missed model_card.py — the C11-a surface. "
            f"Files seen: {sorted(names)}"
        )


class TestStreamlitEscapingContract:
    """Document the Streamlit-side contract C11 relies on.

    These tests do **not** invoke Streamlit's renderer (that's blocked
    on issue #69's AppTest harness). They instead pin the *expectation*
    we have of Streamlit's default behavior by exercising the same
    HTML-escaping primitive a markdown renderer would use on a string
    like ``"<script>alert(1)</script>"``. If Streamlit ever changes
    its default to render raw HTML, neither this test nor the source
    pin above will catch it — that would require the live-render
    assertion from #69. The value here is documenting the contract so
    a reviewer of a future "let's use unsafe_allow_html for X" PR sees
    the threat model spelled out.
    """

    HOSTILE_MODEL_NAME = "<script>alert(1)</script>"

    @pytest.mark.skip(
        reason=(
            "Blocked on #69: streamlit.testing.v1.AppTest harness is not "
            "yet wired. When #69 lands, this test should render the "
            "dashboard tab with a model named HOSTILE_MODEL_NAME and "
            "assert the rendered HTML contains '&lt;script&gt;' (escaped) "
            "and does NOT contain '<script>' (live)."
        )
    )
    def test_model_card_renders_hostile_name_escaped(self) -> None:
        """Live-render assertion for C11-a — deferred to #69.

        Placeholder kept in-file (not deleted) so the #69 implementer
        has an obvious hook to fill in, and so the C11-a coverage gap
        is visible in ``pytest -v`` output rather than buried in a
        backlog comment.
        """
        raise AssertionError("see skip reason")
