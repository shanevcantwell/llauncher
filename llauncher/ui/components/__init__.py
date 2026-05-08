"""Reusable Streamlit UI components (M4).

Components are functions that render small, self-contained slices of UI
and return whatever the surrounding tab needs to drive its logic.
Distinct from ``ui/tabs/``, which compose components into full-tab
layouts. Distinct from ``ui/utils.py``, which holds non-rendering
helpers like ``format_uptime``.

The split exists so M4 tabs can mix-and-match (every tab that targets a
single node uses :func:`node_selector.render_node_selector`, every tab
that surfaces a verb result uses ``ui/utils.py::render_op_result``,
etc.) without each tab re-implementing the pattern in its own dialect.
"""
