"""Streamlit ``AppTest`` tests for the Audit tab (``llauncher/ui/tabs/audit.py``).

The audit tab is a read-only viewer with two dispatch branches on the
sidebar's selected target (per the module docstring's "Scope" section,
issue #64):

* **local** — reads the on-disk JSONL directly via
  :func:`llauncher.core.audit_log.read_entries` (no HTTP hop).
* **remote** — resolves the target through the ``NodeRegistry`` facade and
  fetches over HTTP via :meth:`RemoteNode.read_audit`.

This file pins the render contract for both branches: the "Tail (entries)"
bound and the action/result multiselect filters reach the right facade with
the right arguments, the empty-state banner renders instead of a confusing
empty dataframe, and the remote branch's three outcomes (entries, "log
empty", unreachable, unknown node) each render their documented UI instead of
raising. Every remote assertion runs under ``forbid_direct_http`` so a
regression to raw sockets fails loudly rather than passing quietly.
"""

from __future__ import annotations

from llauncher.core import audit_log
from llauncher.ui.tabs.audit import render_audit_tab


def _multiselect_by_label(at, label):
    for el in at.multiselect:
        if el.label == label:
            return el
    raise AssertionError(
        f"no multiselect labelled {label!r}; saw "
        f"{[e.label for e in at.multiselect]}"
    )


def _number_input_by_label(at, label):
    for el in at.number_input:
        if el.label == label:
            return el
    raise AssertionError(
        f"no number_input labelled {label!r}; saw "
        f"{[e.label for e in at.number_input]}"
    )


class TestAuditTabLocalEmpty:
    """Local target, no entries: the tab must render guidance, not a hole."""

    def test_local_empty_log_renders_info_banner_not_dataframe(
        self, tab_harness, mock_read_entries, mock_registry
    ):
        at = tab_harness(render_audit_tab, "local", mock_registry)

        assert not at.exception
        assert at.header[0].value == "📝 Audit log"
        assert "No audit entries yet" in at.info[0].value
        assert len(at.dataframe) == 0

    def test_local_empty_log_reads_through_audit_log_facade(
        self, tab_harness, mock_read_entries, mock_registry
    ):
        """The local branch never opens the JSONL itself — it goes through
        ``core.audit_log.read_entries``, the seam this fixture doubles."""
        at = tab_harness(render_audit_tab, "local", mock_registry)

        assert not at.exception
        mock_read_entries.assert_called_once()
        assert mock_read_entries.call_args.kwargs["limit"] == 200


class TestAuditTabLocalWithEntries:
    """Local target with entries: renders newest-first in the display table."""

    def test_local_entries_render_as_dataframe_newest_first(
        self, tab_harness, mock_read_entries, make_entry, mock_registry
    ):
        older = make_entry(
            timestamp="2026-07-15T00:00:00+00:00",
            action=audit_log.AuditAction.STARTED,
            message="first",
        )
        newer = make_entry(
            timestamp="2026-07-16T00:00:00+00:00",
            action=audit_log.AuditAction.STOPPED,
            message="second",
        )
        mock_read_entries.return_value = [older, newer]

        at = tab_harness(render_audit_tab, "local", mock_registry)

        assert not at.exception
        assert len(at.info) == 0  # entries present: no empty-state banner
        df = at.dataframe[0].value
        # read_entries returns chronological (oldest-first); the tab reverses
        # for display so the freshest entry is the first row.
        assert df.iloc[0]["message"] == "second"
        assert df.iloc[1]["message"] == "first"

    def test_local_entry_row_flattens_display_columns(
        self, tab_harness, mock_read_entries, make_entry, mock_registry
    ):
        entry = make_entry(
            action=audit_log.AuditAction.SWAPPED,
            result=audit_log.AuditResult.SUCCESS,
            caller="ui",
            port=8001,
            model="qwen",
            message="swapped ok",
        )
        mock_read_entries.return_value = [entry]

        at = tab_harness(render_audit_tab, "local", mock_registry)

        row = at.dataframe[0].value.iloc[0]
        assert row["action"] == "swapped"
        assert row["result"] == "success"
        assert row["caller"] == "ui"
        assert row["port"] == 8001
        assert row["model"] == "qwen"
        assert row["message"] == "swapped ok"


class TestAuditTabFilters:
    """Action/result multiselect widgets post-filter the local entry list."""

    def test_action_filter_narrows_to_selected_actions(
        self, tab_harness, mock_read_entries, make_entry, mock_registry
    ):
        started = make_entry(action=audit_log.AuditAction.STARTED, message="s")
        stopped = make_entry(action=audit_log.AuditAction.STOPPED, message="t")
        mock_read_entries.return_value = [started, stopped]

        at = tab_harness(render_audit_tab, "local", mock_registry)
        _multiselect_by_label(at, "Filter by action").set_value(["started"])
        at.run()

        assert not at.exception
        df = at.dataframe[0].value
        assert list(df["action"]) == ["started"]

    def test_result_filter_narrows_to_selected_results(
        self, tab_harness, mock_read_entries, make_entry, mock_registry
    ):
        ok = make_entry(result=audit_log.AuditResult.SUCCESS, message="ok")
        err = make_entry(result=audit_log.AuditResult.ERROR, message="bad")
        mock_read_entries.return_value = [ok, err]

        at = tab_harness(render_audit_tab, "local", mock_registry)
        _multiselect_by_label(at, "Filter by result").set_value(["error"])
        at.run()

        assert not at.exception
        df = at.dataframe[0].value
        assert list(df["result"]) == ["error"]

    def test_empty_filter_selection_means_all_entries(
        self, tab_harness, mock_read_entries, make_entry, mock_registry
    ):
        """Empty multiselect = no filtering (per the widgets' help text)."""
        started = make_entry(action=audit_log.AuditAction.STARTED, message="s")
        stopped = make_entry(action=audit_log.AuditAction.STOPPED, message="t")
        mock_read_entries.return_value = [started, stopped]

        at = tab_harness(render_audit_tab, "local", mock_registry)

        assert not at.exception
        df = at.dataframe[0].value
        assert len(df) == 2


class TestAuditTabTailControl:
    """The "Tail (entries)" number_input bounds the read (ADR-013 hook)."""

    def test_default_tail_requests_200_entries(
        self, tab_harness, mock_read_entries, mock_registry
    ):
        at = tab_harness(render_audit_tab, "local", mock_registry)

        assert not at.exception
        tail = _number_input_by_label(at, "Tail (entries)")
        assert tail.value == 200
        mock_read_entries.assert_called_once_with(limit=200)

    def test_raising_tail_forwards_new_limit_to_read_entries(
        self, tab_harness, mock_read_entries, mock_registry
    ):
        at = tab_harness(render_audit_tab, "local", mock_registry)

        _number_input_by_label(at, "Tail (entries)").set_value(500)
        at.run()

        assert not at.exception
        mock_read_entries.assert_called_with(limit=500)


class TestAuditTabRemoteSuccess:
    """Remote target: audit entries are fetched via ``RemoteNode.read_audit``."""

    def test_remote_entries_render_through_read_audit_facade(
        self, tab_harness, make_node, registry_factory, forbid_direct_http
    ):
        remote_dict = {
            "timestamp": "2026-07-16T00:00:00+00:00",
            "action": "started",
            "result": "success",
            "caller": "ui",
            "port": 8000,
            "model": "remote-model",
            "message": "remote start",
        }
        node = make_node(name="gpu-rig", read_audit_result=[remote_dict])
        registry = registry_factory([node])

        with forbid_direct_http():
            at = tab_harness(render_audit_tab, "gpu-rig", registry)

        assert not at.exception
        node.read_audit.assert_called_once()
        df = at.dataframe[0].value
        assert df.iloc[0]["model"] == "remote-model"
        assert df.iloc[0]["message"] == "remote start"

    def test_remote_entries_forward_single_value_filters_over_the_wire(
        self, tab_harness, make_node, registry_factory
    ):
        """A single-value selection is pushed down the wire (issue #118); the
        tab still post-filters locally, so the effect is observable either
        way — this test pins the over-the-wire kwargs."""
        node = make_node(name="gpu-rig", read_audit_result=[])
        registry = registry_factory([node])

        at = tab_harness(render_audit_tab, "gpu-rig", registry)
        _multiselect_by_label(at, "Filter by action").set_value(["started"])
        _multiselect_by_label(at, "Filter by result").set_value(["success"])
        at.run()

        assert not at.exception
        node.read_audit.assert_called_with(
            limit=200, action_filter="started", result_filter="success"
        )

    def test_remote_multi_value_filter_is_not_pushed_over_the_wire(
        self, tab_harness, make_node, registry_factory
    ):
        """Multi-value selections can't be expressed in the single-value wire
        filter, so the tab fetches unfiltered and relies on the in-memory
        post-filter instead (see the "issue #118" comment in audit.py)."""
        node = make_node(name="gpu-rig", read_audit_result=[])
        registry = registry_factory([node])

        at = tab_harness(render_audit_tab, "gpu-rig", registry)
        _multiselect_by_label(at, "Filter by action").set_value(
            ["started", "stopped"]
        )
        at.run()

        assert not at.exception
        node.read_audit.assert_called_with(
            limit=200, action_filter=None, result_filter=None
        )

    def test_remote_empty_log_renders_info_banner(
        self, tab_harness, make_node, registry_factory
    ):
        node = make_node(name="gpu-rig", read_audit_result=[])
        registry = registry_factory([node])

        at = tab_harness(render_audit_tab, "gpu-rig", registry)

        assert not at.exception
        assert "No audit entries yet" in at.info[0].value


class TestAuditTabRemoteFailure:
    """Remote target failure modes: unreachable node, unknown node."""

    def test_remote_unreachable_node_renders_error_not_exception(
        self, tab_harness, make_node, registry_factory, forbid_direct_http
    ):
        """``RemoteNode.read_audit`` returning ``None`` means "node offline
        or unreachable" (per its docstring) — the tab must render an
        ``st.error``, never raise or render an empty dataframe."""
        node = make_node(name="gpu-rig", read_audit_result=None)
        registry = registry_factory([node])

        with forbid_direct_http():
            at = tab_harness(render_audit_tab, "gpu-rig", registry)

        assert not at.exception
        assert len(at.dataframe) == 0
        assert any(
            "offline or unreachable" in e.value for e in at.error
        )

    def test_remote_unknown_node_renders_error_naming_the_target(
        self, tab_harness, mock_aggregator, registry_factory
    ):
        """A target that resolves to no node in the registry (e.g. removed
        after selection) renders a targeted error, not a crash."""
        registry = registry_factory([])  # no node named "ghost-rig"

        at = tab_harness(render_audit_tab, "ghost-rig", registry)

        assert not at.exception
        assert any("ghost-rig" in e.value for e in at.error)
        assert any("Unknown node" in e.value for e in at.error)

    def test_remote_target_with_no_registry_renders_error_not_exception(
        self, tab_harness
    ):
        """``registry=None`` (documented backward-compatible default) must
        degrade to the same error banner, never an ``AttributeError``."""
        at = tab_harness(render_audit_tab, "gpu-rig", None)

        assert not at.exception
        assert any("Unknown node" in e.value for e in at.error)
