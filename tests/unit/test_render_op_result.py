"""Unit tests for ``ui/utils.py::render_op_result`` (issue #51, M4 Slice 14).

The renderer is the single point of truth for translating ADR-LLNCH-010/011
operation envelopes into Streamlit feedback. Tests are split between:

- ``classify_action`` — pure-logic; covers every documented action
  string from every verb (start / stop / swap / delete_model) plus
  defensive fallthrough for unknown / empty inputs.
- ``render_op_result`` — Streamlit wrapper; mocked via
  ``patch("llauncher.ui.utils.st")`` per the existing project pattern.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest

from llauncher.ui.utils import (
    OpResultSeverity,
    _SEVERITY_ICONS,
    _default_message_for,
    classify_action,
    render_op_result,
)


# ---------------------------------------------------------------------------
# classify_action — pure-logic
# ---------------------------------------------------------------------------


class TestClassifyActionSuccess:
    """All four ``SUCCESS`` actions across the four verbs."""

    @pytest.mark.parametrize(
        "action",
        ["started", "stopped", "swapped", "deleted"],
    )
    def test_success_actions(self, action: str) -> None:
        assert classify_action(action) is OpResultSeverity.SUCCESS


class TestClassifyActionInfo:
    """Idempotent no-ops — should be quiet but visible."""

    @pytest.mark.parametrize(
        "action",
        # ``stopping`` is the issue-#140 async-accept envelope from a
        # remote agent: acknowledged, completion pending — INFO, not
        # SUCCESS (the outcome hasn't landed yet) and not ERROR.
        ["already_running", "already_empty", "not_found", "stopping"],
    )
    def test_info_actions(self, action: str) -> None:
        assert classify_action(action) is OpResultSeverity.INFO


class TestClassifyActionWarning:
    """Recoverable failures the user must still notice."""

    @pytest.mark.parametrize(
        "action",
        ["rolled_back", "rejected_preflight", "rejected_in_progress"],
    )
    def test_warning_actions(self, action: str) -> None:
        assert classify_action(action) is OpResultSeverity.WARNING


class TestClassifyActionError:
    """Hard failures that need human action."""

    @pytest.mark.parametrize(
        "action",
        [
            "rejected_occupied",
            "rejected_empty",
            "rejected_stop_failed",
            "rejected_in_use",
            "failed",
            "error",
        ],
    )
    def test_error_actions(self, action: str) -> None:
        assert classify_action(action) is OpResultSeverity.ERROR


class TestClassifyActionFallthrough:
    """Defensive: unknown / empty input must NOT classify as success.

    The renderer has to assume "unrecognized action" means "something is
    wrong" — silently treating unknown shapes as success would mask bugs
    (a future MCP tool that mistypes ``swappped``, say).
    """

    @pytest.mark.parametrize(
        "action",
        ["", None, "not_a_real_action", "Started", "ROLLBACK"],
    )
    def test_unknown_classifies_as_error(self, action) -> None:
        assert classify_action(action) is OpResultSeverity.ERROR


# ---------------------------------------------------------------------------
# render_op_result — Streamlit wrapper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeResult:
    """Minimal stand-in for ``operations/*Result`` envelopes."""

    success: bool
    action: str
    message: str = ""


class TestRenderOpResultDataclassInput:
    """Verify the renderer reads action/message off frozen dataclasses."""

    def test_success_emits_toast_only(self) -> None:
        """``started`` produces a single toast and no sticky panel.

        SUCCESS is toast-only because the tab's redraw already reflects
        the new running state — a sticky banner would just nag.
        """
        result = _FakeResult(success=True, action="started", message="Started foo")

        with patch("llauncher.ui.utils.st") as mock_st:
            severity = render_op_result(result, verb_label="Start")

        assert severity is OpResultSeverity.SUCCESS
        mock_st.toast.assert_called_once()
        toast_args, toast_kwargs = mock_st.toast.call_args
        assert toast_args[0] == "Started foo"
        assert toast_kwargs["icon"] == _SEVERITY_ICONS[OpResultSeverity.SUCCESS]
        # No sticky panel for SUCCESS.
        mock_st.warning.assert_not_called()
        mock_st.error.assert_not_called()

    def test_info_emits_toast_only(self) -> None:
        """``already_running`` is a no-op confirmation; toast only."""
        result = _FakeResult(
            success=True, action="already_running", message="foo already on 8081"
        )

        with patch("llauncher.ui.utils.st") as mock_st:
            severity = render_op_result(result, verb_label="Start")

        assert severity is OpResultSeverity.INFO
        mock_st.toast.assert_called_once()
        mock_st.warning.assert_not_called()
        mock_st.error.assert_not_called()

    def test_warning_emits_toast_and_sticky_warning(self) -> None:
        """``rolled_back`` needs a sticky panel — a toast disappears."""
        result = _FakeResult(
            success=False,
            action="rolled_back",
            message="swap failed; restored old-model",
        )

        with patch("llauncher.ui.utils.st") as mock_st:
            severity = render_op_result(result, verb_label="Swap")

        assert severity is OpResultSeverity.WARNING
        mock_st.toast.assert_called_once()
        mock_st.warning.assert_called_once()
        # Sticky panel includes the verb label so users can tell which
        # action's outcome they're reading.
        warning_text = mock_st.warning.call_args[0][0]
        assert warning_text.startswith("Swap:")
        assert "restored" in warning_text
        mock_st.error.assert_not_called()

    def test_error_emits_toast_and_sticky_error(self) -> None:
        """``rejected_in_use`` is a hard failure; sticky red panel."""
        result = _FakeResult(
            success=False,
            action="rejected_in_use",
            message="model is running on port 8081",
        )

        with patch("llauncher.ui.utils.st") as mock_st:
            severity = render_op_result(result, verb_label="Delete model")

        assert severity is OpResultSeverity.ERROR
        mock_st.toast.assert_called_once()
        mock_st.error.assert_called_once()
        error_text = mock_st.error.call_args[0][0]
        assert error_text.startswith("Delete model:")
        mock_st.warning.assert_not_called()


class TestRenderOpResultDictInput:
    """The renderer accepts ``.to_dict()`` envelopes too.

    Some MCP / HTTP code paths hand back a JSON-shaped dict instead of
    the frozen dataclass. Both shapes must work without conversion at
    the call site.
    """

    def test_dict_envelope_is_read_correctly(self) -> None:
        envelope = {
            "success": True,
            "action": "swapped",
            "message": "now serving new-model",
            "port": 8081,
        }

        with patch("llauncher.ui.utils.st") as mock_st:
            severity = render_op_result(envelope, verb_label="Swap")

        assert severity is OpResultSeverity.SUCCESS
        mock_st.toast.assert_called_once()
        assert mock_st.toast.call_args[0][0] == "now serving new-model"

    def test_dict_with_error_field_falls_back_to_error(self) -> None:
        """Some legacy envelopes use ``error`` instead of ``message``.

        ``operations/`` v2 always uses ``message``, but the MCP
        ``add_model`` and ``update_model`` tools (config.py) use
        ``error`` on the failure path. The renderer should pick up
        either, since both flow through this single point.
        """
        envelope = {
            "success": False,
            "action": "error",
            "error": "Model not found: ghost",
        }

        with patch("llauncher.ui.utils.st") as mock_st:
            severity = render_op_result(envelope, verb_label="Update")

        assert severity is OpResultSeverity.ERROR
        # Toast text falls through ``message`` → ``error``.
        assert "Model not found" in mock_st.toast.call_args[0][0]


class TestRenderOpResultDefaults:
    """Defensive rendering for envelopes with missing fields."""

    def test_unknown_action_classifies_as_error_and_renders_sticky(self) -> None:
        """A typo or future-version action shouldn't silently succeed."""
        with patch("llauncher.ui.utils.st") as mock_st:
            severity = render_op_result(
                _FakeResult(success=False, action="swappped", message=""),
                verb_label="Swap",
            )

        assert severity is OpResultSeverity.ERROR
        mock_st.error.assert_called_once()

    def test_empty_message_falls_back_to_action_string(self) -> None:
        """When ``message`` is empty we synthesize a stub from action+verb."""
        with patch("llauncher.ui.utils.st") as mock_st:
            render_op_result(
                _FakeResult(success=True, action="started", message=""),
                verb_label="Start",
            )

        toast_text = mock_st.toast.call_args[0][0]
        assert "Start" in toast_text
        assert "started" in toast_text

    def test_default_verb_label(self) -> None:
        """Callers may omit ``verb_label`` for one-off ops."""
        with patch("llauncher.ui.utils.st") as mock_st:
            render_op_result(
                _FakeResult(success=False, action="error", message="generic failure"),
            )

        # Sticky panel uses the default "Operation" prefix.
        error_text = mock_st.error.call_args[0][0]
        assert error_text.startswith("Operation:")


# ---------------------------------------------------------------------------
# Public-surface guards (locked-in for M4 Slice 13 / 14 consumers)
# ---------------------------------------------------------------------------


class TestSeverityIconCoverage:
    """Every severity must have an icon — guards against accidental drift."""

    def test_every_severity_has_an_icon(self) -> None:
        """Adding a new ``OpResultSeverity`` member must update the icon table."""
        for severity in OpResultSeverity:
            assert severity in _SEVERITY_ICONS, (
                f"Add {severity!r} to _SEVERITY_ICONS in ui/utils.py"
            )

    def test_icons_are_distinct(self) -> None:
        """Each severity gets a visually distinct icon — no copy-paste collisions."""
        icons = list(_SEVERITY_ICONS.values())
        assert len(icons) == len(set(icons)), (
            f"Duplicate icons in _SEVERITY_ICONS: {icons}"
        )


class TestDefaultMessageForActionlessEnvelope:
    """Phase 2b (test-coverage-plan.md) pin: 2026-08-20 review finding.

    ``_default_message_for`` has two branches: an ``action`` present (falls
    back to ``"{verb_label}: {action}"``, already pinned by
    ``TestRenderOpResultDefaults.test_empty_message_falls_back_to_action_string``
    above) and ``action`` falsy/``None`` (a defensive branch guarding a
    handcrafted envelope — e.g. a future MCP tool that mirrors the
    ``operations/`` result shape but forgets to populate ``action``). This
    pins the *actionless* branch directly, both at the unit level and
    through the full ``render_op_result`` renderer.
    """

    @pytest.mark.parametrize("action", [None, ""])
    def test_actionless_envelope_falls_back_to_no_action_message(self, action) -> None:
        assert _default_message_for(action, "Start") == "Start returned no action"

    def test_render_op_result_with_actionless_empty_message_envelope(self) -> None:
        """The full renderer surfaces the no-action fallback as its toast."""
        with patch("llauncher.ui.utils.st") as mock_st:
            severity = render_op_result(
                _FakeResult(success=False, action=None, message=""),
                verb_label="Swap",
            )

        assert severity is OpResultSeverity.ERROR  # unrecognized action -> ERROR
        toast_text = mock_st.toast.call_args[0][0]
        assert toast_text == "Swap returned no action"
