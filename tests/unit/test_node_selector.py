"""Unit tests for the M4 node_selector component (issue #48).

The component splits cleanly into a pure-logic option-builder
(:func:`compute_node_options`) and a thin Streamlit wrapper
(:func:`render_node_selector`). The pure helper gets the bulk of the
test attention; the Streamlit wrapper is exercised by mocking the
``st`` module per the existing project pattern (see
``tests/unit/test_dashboard.py`` for precedent).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from llauncher.ui.components.node_selector import (
    LOCAL_NODE,
    TARGET_NODE_KEY,
    compute_node_options,
    render_node_selector,
)


# ---------------------------------------------------------------------------
# compute_node_options — pure-logic helper
# ---------------------------------------------------------------------------


class TestComputeNodeOptions:
    """Pure-logic tests; no Streamlit involvement."""

    def test_empty_registry_returns_local_only(self) -> None:
        """No peers configured → "local" is still always selectable."""
        assert compute_node_options([]) == [LOCAL_NODE]

    def test_preserves_registry_iteration_order(self) -> None:
        """Peers appear in iteration order; ``local`` is forced first."""
        peers = [
            SimpleNamespace(name="alpha"),
            SimpleNamespace(name="bravo"),
            SimpleNamespace(name="charlie"),
        ]

        assert compute_node_options(peers) == [
            LOCAL_NODE,
            "alpha",
            "bravo",
            "charlie",
        ]

    def test_dedups_peer_named_local(self) -> None:
        """A peer literally named ``local`` does not get a second entry.

        The synthetic ``local`` wins. Whatever the user meant by their
        ``local`` peer is theirs to disambiguate in nodes.json.
        """
        peers = [
            SimpleNamespace(name="local"),
            SimpleNamespace(name="alpha"),
        ]

        assert compute_node_options(peers) == [LOCAL_NODE, "alpha"]

    def test_dedups_repeated_peer_names(self) -> None:
        """Duplicate names in the iteration sequence are collapsed."""
        peers = [
            SimpleNamespace(name="alpha"),
            SimpleNamespace(name="alpha"),
            SimpleNamespace(name="bravo"),
        ]

        assert compute_node_options(peers) == [LOCAL_NODE, "alpha", "bravo"]

    def test_skips_objects_without_name_attribute(self) -> None:
        """Defensive: malformed registry entries are silently ignored.

        Registry corruption (e.g., a partially-deserialized JSON entry)
        should not crash the selector — it should still render with
        ``local`` plus whatever entries are well-formed.
        """
        peers = [
            SimpleNamespace(name="alpha"),
            object(),  # no .name
            SimpleNamespace(name="bravo"),
        ]

        assert compute_node_options(peers) == [LOCAL_NODE, "alpha", "bravo"]


# ---------------------------------------------------------------------------
# render_node_selector — Streamlit wrapper
# ---------------------------------------------------------------------------


class TestRenderNodeSelector:
    """Wrapper tests: mock ``st`` and assert it's called with the right shape."""

    def test_passes_options_and_key_to_selectbox(self) -> None:
        """Wrapper feeds ``compute_node_options`` output into ``st.selectbox``.

        The selectbox call is the contract this component exposes; both
        the option list and the persistence key must be visible to the
        Streamlit machinery so other tabs can share the selection.
        """
        registry = MagicMock()
        registry.__iter__.return_value = iter(
            [SimpleNamespace(name="alpha"), SimpleNamespace(name="bravo")]
        )

        with patch(
            "llauncher.ui.components.node_selector.st"
        ) as mock_st:
            mock_st.selectbox.return_value = "alpha"

            result = render_node_selector(registry)

        assert result == "alpha"
        mock_st.selectbox.assert_called_once()
        _, kwargs = mock_st.selectbox.call_args
        assert kwargs["options"] == [LOCAL_NODE, "alpha", "bravo"]
        assert kwargs["key"] == TARGET_NODE_KEY

    def test_default_returns_local_for_empty_registry(self) -> None:
        """An empty registry still produces a usable selector."""
        registry = MagicMock()
        registry.__iter__.return_value = iter([])

        with patch(
            "llauncher.ui.components.node_selector.st"
        ) as mock_st:
            # Streamlit's selectbox defaults to the first option when the
            # session-state key is unset; we mirror that here.
            mock_st.selectbox.return_value = LOCAL_NODE

            result = render_node_selector(registry)

        assert result == LOCAL_NODE
        _, kwargs = mock_st.selectbox.call_args
        assert kwargs["options"] == [LOCAL_NODE]

    def test_custom_key_is_honored(self) -> None:
        """Tabs that need an isolated selection can pass their own key.

        The default :data:`TARGET_NODE_KEY` is the cross-tab consensus
        key. Tests, modal dialogs, or special-purpose tabs may need a
        scoped key.
        """
        registry = MagicMock()
        registry.__iter__.return_value = iter([])

        with patch(
            "llauncher.ui.components.node_selector.st"
        ) as mock_st:
            mock_st.selectbox.return_value = LOCAL_NODE

            render_node_selector(registry, key="ui.modal.target_node")

        _, kwargs = mock_st.selectbox.call_args
        assert kwargs["key"] == "ui.modal.target_node"

    def test_label_and_help_passthrough(self) -> None:
        """Label/help are surface-level customizations; honored verbatim."""
        registry = MagicMock()
        registry.__iter__.return_value = iter([])

        with patch(
            "llauncher.ui.components.node_selector.st"
        ) as mock_st:
            mock_st.selectbox.return_value = LOCAL_NODE

            render_node_selector(
                registry,
                label="Pick a target",
                help="for the swap dialog",
            )

        args, kwargs = mock_st.selectbox.call_args
        # label is positional in our wrapper
        assert args[0] == "Pick a target"
        assert kwargs["help"] == "for the swap dialog"


# ---------------------------------------------------------------------------
# Public-surface guards
# ---------------------------------------------------------------------------


class TestPublicSurface:
    """Lock the names other tabs will import — ``key`` and ``LOCAL_NODE``.

    These tests guard against accidental rename in M4 Slice 13 / 14 by
    making the constants part of the test contract.
    """

    def test_target_node_key_is_namespaced(self) -> None:
        """The session-state key must live under the ``ui.`` namespace.

        Other session keys in the app are bare names (``state``,
        ``registry``, ``selected_node``, …). The ``ui.`` prefix makes
        the M4 component-owned keys grep-friendly.
        """
        assert TARGET_NODE_KEY.startswith("ui.")

    def test_local_node_constant_matches_protocol(self) -> None:
        """``LOCAL_NODE`` is the literal that ``model_card.py`` checks.

        Per the existing local-vs-remote dispatch in ``_handle_start``,
        the string ``"local"`` (lowercase) is the discriminator. If
        this constant ever changed, every dispatch site would need
        updating in lockstep.
        """
        assert LOCAL_NODE == "local"
