"""Tests for the Audit tab (llauncher/ui/tabs/audit.py).

M4 Slice 13 / issue #50, stage 1. The tab is local-only in this slice;
stage 2 / a follow-up issue will wire remote-node audit access.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from llauncher.core.audit_log import AuditAction, AuditEntry, AuditResult


def _entry(
    *,
    action: AuditAction = AuditAction.STARTED,
    result: AuditResult = AuditResult.SUCCESS,
    ts_offset_seconds: int = 0,
    model: str | None = "m",
    port: int | None = 8080,
) -> AuditEntry:
    """Build a deterministic AuditEntry for tests."""
    base = datetime(2026, 5, 9, tzinfo=timezone.utc)
    return AuditEntry(
        timestamp=(base + timedelta(seconds=ts_offset_seconds)).isoformat(),
        action=action,
        result=result,
        caller="ui",
        port=port,
        model=model,
        from_model=None,
        pid=None,
        message=f"{action.value}@{ts_offset_seconds}",
    )


def _patched_st():
    """Return a context-manager stack for streamlit mocking.

    The audit tab calls a fixed sequence of widgets; tests configure
    return values via the ``mock_st`` fixture's attributes.
    """
    return patch("llauncher.ui.tabs.audit.st")


class TestEmptyState:
    """When the audit log is empty, the dataframe must NOT render."""

    def test_empty_state_renders_info_panel_and_no_dataframe(self):
        from llauncher.ui.tabs.audit import render_audit_tab

        with _patched_st() as mock_st, patch(
            "llauncher.ui.tabs.audit.audit_log.read_entries", return_value=[]
        ):
            mock_st.number_input.return_value = 200
            mock_st.multiselect.return_value = []

            render_audit_tab("local")

            mock_st.info.assert_called_once()
            mock_st.dataframe.assert_not_called()


class TestNonEmptyRendering:
    """Populated audit log renders a newest-first dataframe."""

    def test_non_empty_renders_dataframe_newest_first(self):
        from llauncher.ui.tabs.audit import render_audit_tab

        # Chronological (oldest first), per audit_log.read_entries contract.
        oldest = _entry(ts_offset_seconds=0, model="oldest")
        middle = _entry(ts_offset_seconds=10, model="middle")
        newest = _entry(ts_offset_seconds=20, model="newest")

        with _patched_st() as mock_st, patch(
            "llauncher.ui.tabs.audit.audit_log.read_entries",
            return_value=[oldest, middle, newest],
        ):
            mock_st.number_input.return_value = 200
            mock_st.multiselect.return_value = []

            render_audit_tab("local")

            mock_st.dataframe.assert_called_once()
            df = mock_st.dataframe.call_args[0][0]
            # First row in the rendered frame should be the most recent
            # entry — i.e. the *last* item read off disk.
            assert df.iloc[0]["model"] == "newest"
            assert df.iloc[-1]["model"] == "oldest"


class TestActionFilter:
    """Action multiselect narrows rendered rows."""

    def test_action_filter_narrows_rows(self):
        from llauncher.ui.tabs.audit import render_audit_tab

        entries = [
            _entry(action=AuditAction.STARTED, ts_offset_seconds=0, model="a"),
            _entry(action=AuditAction.STOPPED, ts_offset_seconds=1, model="b"),
            _entry(action=AuditAction.STARTED, ts_offset_seconds=2, model="c"),
            _entry(action=AuditAction.SWAPPED, ts_offset_seconds=3, model="d"),
            _entry(action=AuditAction.STARTED, ts_offset_seconds=4, model="e"),
        ]

        with _patched_st() as mock_st, patch(
            "llauncher.ui.tabs.audit.audit_log.read_entries", return_value=entries
        ):
            mock_st.number_input.return_value = 200
            # First multiselect call = action filter, second = result filter.
            mock_st.multiselect.side_effect = [["started"], []]

            render_audit_tab("local")

            mock_st.dataframe.assert_called_once()
            df = mock_st.dataframe.call_args[0][0]
            assert len(df) == 3
            assert set(df["action"].tolist()) == {"started"}
            assert set(df["model"].tolist()) == {"a", "c", "e"}


class TestLimitForwarding:
    """The Tail input is forwarded as ``limit=`` to ``read_entries``."""

    def test_limit_input_forwards_to_read_entries(self):
        from llauncher.ui.tabs.audit import render_audit_tab

        with _patched_st() as mock_st, patch(
            "llauncher.ui.tabs.audit.audit_log.read_entries", return_value=[]
        ) as mock_read:
            mock_st.number_input.return_value = 50
            mock_st.multiselect.return_value = []

            render_audit_tab("local")

            mock_read.assert_called_once_with(limit=50)


class TestRemoteTargetCaption:
    """Non-local targets render a "local-only" caption."""

    def test_remote_target_renders_caption(self):
        from llauncher.ui.tabs.audit import render_audit_tab

        with _patched_st() as mock_st, patch(
            "llauncher.ui.tabs.audit.audit_log.read_entries", return_value=[]
        ):
            mock_st.number_input.return_value = 200
            mock_st.multiselect.return_value = []

            render_audit_tab("remote-1")

            mock_st.caption.assert_called_once()
            caption_text = mock_st.caption.call_args[0][0]
            assert "remote-node audit access is not yet wired" in caption_text

    def test_local_target_does_not_render_caption(self):
        """Sanity check: the local case should NOT show the disclaimer."""
        from llauncher.ui.tabs.audit import render_audit_tab

        with _patched_st() as mock_st, patch(
            "llauncher.ui.tabs.audit.audit_log.read_entries", return_value=[]
        ):
            mock_st.number_input.return_value = 200
            mock_st.multiselect.return_value = []

            render_audit_tab("local")

            mock_st.caption.assert_not_called()
