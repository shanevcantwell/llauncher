# Streamlit UI Harness Plan (issue #69)

**Status:** draft — design only, no execution yet.
**Predecessor:** `docs/plans/test-coverage-plan.md` (Phase D landed; UI deferred here).
**Slot:** Phase E of the test-coverage track. Phase B explicitly deferred `ui/tabs/*` to this issue; this plan picks up that scope.
**Last touched:** 2026-05-24.

---

## 1. Audit findings (per tab module)

All seven modules use only the basic widget surface — **no `@st.dialog`, no `@st.fragment` (or experimental_), no `st.pills`**. The upstream-bug class the research comment flagged (#7711, #11497, #9786) is therefore **not a blocker** for this codebase. The remaining gotcha is `st.form` (`forms.py`, `nodes.py`), which AppTest handles cleanly via the "set inputs → click `form_submit_button` → `run()`" pattern.

- **`audit.py`** (187 lines, ~37% cov): `st.number_input`, two `st.multiselect`, `st.dataframe`, `st.error`, `st.info`. No forms. Backend: `core.audit_log.read_entries` (filesystem) and `NodeRegistry.get_node().read_audit()` (HTTP). Pure read path, no session_state. **Lowest-gotcha module.**
- **`dashboard.py`** (168 lines, ~62% cov already via `test_dashboard.py`): `st.header`, `st.caption`, `st.subheader`, `st.info`, `st.dataframe`. View-only since #50 Stage 2. No widgets that require interaction. Backend: `LauncherState`, `RemoteAggregator`. The non-render helpers (`get_servers_to_display`, `get_models_to_display`) are already covered.
- **`forms.py`** (451 lines, 5% cov): TWO `st.form` blocks (`add_model_form`, `edit_model_form`), each with ~20 widgets (`text_input`, `number_input`, `selectbox`, `checkbox`, nested `st.columns`, `st.expander` for advanced options) and `st.form_submit_button`. Reads `st.session_state[f"editing_{name}"]`; deletes that key on submit/cancel. Backend: `LauncherState.add_model/update_model`, `ConfigStore`, `ModelConfig.from_dict_unvalidated` (lazy-imported). **Largest payoff (144 lines), but conceptually simple — two forms with deterministic submit paths.**
- **`model_card.py`** (393 lines, 45% cov): per-card view rendered inside `st.expander`, several `st.button` actions (Start/Stop/Edit/Refresh), `st.code` for logs, `st.toast` + `st.rerun()` after actions, writes `st.session_state[f"editing_{model_name}"] = True`. Function `_render_eviction_dialog` is a **plain function, not `@st.dialog`** — name is historical. Backend: `operations as ops` (start/stop/swap/eviction), `core.process.stream_logs`, `RemoteAggregator`, `render_port_picker` component. Per-card rendering is keyed by `node_name`/`model_name`, so iteration order matters for widget indexing.
- **`model_registry.py`** (116 lines, 8% cov): `st.subheader`, `st.info`, `st.dataframe` with `st.column_config.TextColumn`. View-only. Backend: `LauncherState.models`, `RemoteAggregator.get_all_models`. **Second-lowest gotcha.**
- **`models.py`** (153 lines): composition root. `st.header`, `st.divider`, `st.expander` (wraps `render_add_model`), `st.subheader`, iterates over models calling `render_model_card`/`render_edit_model` based on `editing_<name>` session_state. Most of its lines are pure dispatch — coverage rises mechanically as the leaves are tested.
- **`nodes.py`** (250 lines, 5% cov): `st.expander` + `st.form("add_node_form")` containing `text_input`/`number_input` + TWO `form_submit_button`s (Test / Add — both submit, behavior branches on which fired). Per-node `st.button("Remove")`, `st.button("Refresh All")`, `st.toast`, `st.rerun`. Backend: `NodeRegistry.add_node/remove_node/refresh_all`, `RemoteNode` ping. Dual-submit-button form is the only mild gotcha — assert on the `.click()` of the right button.

---

## 2. Harness design

### Where the fixture lives
`tests/unit/ui/conftest.py` (new directory `tests/unit/ui/`). Co-locating new tests under `tests/unit/ui/test_<tab>_apptest.py` keeps the existing `test_<tab>.py` files (which mock `st` directly) untouched and clearly separates the two strategies. Existing tests stay — they cover pure helpers that don't need a Streamlit runtime.

### Streamlit entry point for `AppTest`
`AppTest.from_file("llauncher/ui/app.py")` against the `main()` defined there. But two concerns:
1. `app.py` calls `st.set_page_config` at module top, then `main()` is called only under `if __name__ == "__main__"`. AppTest runs the *script*, so module-level code runs and `main()` does not. **Decision:** the harness sets `at = AppTest.from_file(...)` and adds a small shim — either (a) a sibling `tests/unit/ui/_app_entry.py` that imports and calls `main()`, or (b) configure `AppTest.from_function(main)`. **Recommend `from_function(main)`** — no new file, no shim, and `main()` is already the natural entry.
2. `main()` calls `is_agent_ready(registry)` and `st.stop()` on failure. The fixture must pre-seed a registry whose `is_local_agent_ready()` returns `True`, otherwise every test hits the down-banner branch.

### Fixture shape
```python
@pytest.fixture
def ui_app(monkeypatch, tmp_path):
    """Return a configured AppTest for llauncher.ui.app.main, pre-seeded
    with mocked LauncherState / NodeRegistry / RemoteAggregator and a
    ready agent."""
    # 1. Patch the three get_* factories in llauncher.ui.app to return
    #    pre-built MagicMocks (or real instances with mocked backends).
    # 2. Patch is_agent_ready -> True.
    # 3. Patch backend modules used by tab leaves (operations as ops,
    #    audit_log.read_entries, RemoteNode.read_audit, etc.).
    # 4. Return a factory: def build(session_state=None, tab=0) -> AppTest.
```
The factory returns an `AppTest` already `.run()`-ed once with optional `session_state` pre-seeded; tests call `at.tabs[tab].run()` and assert.

### Convention
```python
def test_audit_tab_renders_empty_state(ui_app):
    at = ui_app(tab=3)              # 3 = Audit per app.py:142
    at.tabs[3].run()
    assert any("No audit entries yet" in i.value for i in at.tabs[3].info)
```
Helpers go in `tests/unit/ui/_helpers.py`: `click_button_by_label(at, label)`, `set_form_inputs(form, **kwargs)`, `assert_toast(at, contains=...)`.

### Handling gotchas
- **Forms:** `at.tabs[i].text_input(key=...).set_value(...)`; final `at.tabs[i].button(key="FormSubmitter:add_model_form-Add Model").click()`; then `at.run()`. Submit buttons in `st.form` are addressed by the synthetic `FormSubmitter:` key Streamlit assigns.
- **`st.rerun`:** AppTest automatically loops re-runs up to `at.run()`'s default budget; explicit `at.run()` after any click handles it.
- **Per-card iteration / dynamic keys:** the harness pre-seeds `state.models = {"model1": cfg}` so widget keys are deterministic (`edit_local_model1_enabled`, etc.). The helpers use `key=`-based lookup rather than positional indexing — robust against reorderings.
- **Session-state writes (`editing_<name>`):** test by clicking Edit, calling `at.run()`, then asserting `at.session_state["editing_model1"] is True`. For the edit-form path, pre-seed `at.session_state["editing_model1"] = True` before the first `run()`.

---

## 3. Pilot tab: **`audit.py`**

**Justification:**
1. Smallest interaction surface — three widgets (`number_input`, two `multiselect`), no forms, no buttons, no `st.rerun`.
2. No backend mutation — only reads (`audit_log.read_entries`, `RemoteNode.read_audit`). Easy to mock with a static list.
3. Currently at ~37% cov, with ~120 missed lines — meaningful payoff for the proving-ground tab.
4. Branches well: local path, remote path, missing registry, offline node, empty result, filter-by-action, filter-by-result. All reachable from `at.tabs[3]` plus session_state pre-seed of the node selector.
5. Forces the harness to solve every problem the others will need (mocking backend, pre-seeding selector state, navigating tabs, asserting on `dataframe` / `info` / `error`) without piling on the form gotcha.

Deliverable: `tests/unit/ui/test_audit_apptest.py` with ~8 tests covering each branch in `render_audit_tab`, lifting `audit.py` from ~37% to >90%. The fixture and `_helpers.py` will land alongside this test file as part of the same PR — they become the contract that subsequent tabs reuse.

---

## 4. Rollout sequence

After the pilot harness lands:

| Order | Tab | Effort | Coverage payoff | Gotcha notes |
|---|---|---|---:|---|
| 1 | `model_registry.py` | **Small** | 50 stmts / 46 missed → ~+0.9pt total | View-only, mirrors audit pattern. Sanity check that helpers generalize. |
| 2 | `models.py` (composition root) | **Small** | Low direct, but enables card iteration | Mostly verifies header/expander/iteration; leaves heavy lifting to card/forms tests. |
| 3 | `model_card.py` | **Medium** | 146 stmts / 81 missed → ~+1.6pt total | Many buttons, `st.rerun`, `ops` calls. Mock `ops.start/stop/swap` return values; assert toasts and session-state mutations. `_render_eviction_dialog` is plain function — exercise via the port-picker-occupied branch. |
| 4 | `forms.py` | **Large** | 151 stmts / 144 missed → ~+2.9pt total | Two large forms. Split tests: add-form happy path, add-form validation failures, edit-form happy path, edit-form cancel (session_state cleanup), advanced-options branch. **Risk: 20+ widgets per form — write a `make_add_model_payload(**overrides)` helper to keep tests dense.** |
| 5 | `nodes.py` | **Large** | 117 stmts / 111 missed → ~+2.2pt total | Form with dual submit buttons (Test / Add). Remove-button per row. Mock `NodeRegistry.add_node` / `RemoteNode.test_connection`. **Risk: per-node dynamic keys — pre-seed the registry with exactly the nodes the test expects.** |
| — | `dashboard.py` | Small follow-up | Already 62%; ~+0.8pt if pushed to 95% | Mostly view-only; existing helper tests cover non-render logic. One or two AppTest cases for the empty-state branches close the gap. |

Sessions 1-3 are "small" (single sitting each). Forms and nodes each warrant their own session given the surface area.

---

## 5. Acceptance criteria

**Per tab:**
- Module coverage ≥ 90% line.
- All branches reachable via UI interaction are exercised by at least one AppTest case (not just helper-function unit tests).
- Tests run without launching a browser; pure pytest invocation.

**For the issue overall:**
- Harness fixture (`tests/unit/ui/conftest.py`) and helpers (`tests/unit/ui/_helpers.py`) merged.
- All seven tab modules at ≥90% line coverage.
- Combined (UI + non-UI) coverage published in PR description; expected new total ≈ 91-93% (from 79% baseline + ~12pt of UI).
- `pyproject.toml` `[tool.coverage.run]` updates: remove the `llauncher/ui/*` omit, so the existing `--cov-fail-under=93` floor in `pytest.ini` now applies to the full source. Re-baseline floor to a value ~2pt below measured (likely `--cov-fail-under=90`).
- `docs/plans/test-coverage-plan.md` updated: Phase E recorded as landed; the "UI deferred to #69" notes in Phase B/D removed.

---

## 6. Risks and unknowns

- **Streamlit version:** repo declares `streamlit>=1.30.0`; local install is `1.56.0`. `AppTest` is stable since 1.28. No pin tightening required, but CI should install at least 1.30 (it does, via the `ui` extra). **Recommend** bumping the floor to `>=1.32.0` in `pyproject.toml` when this lands — that's the first version where `st.session_state` interactions in AppTest are documented as stable.
- **CI cost:** AppTest reruns the script per `at.run()`. Estimating ~40-60 tests × ~200 ms each ≈ 8-12 s added to CI. Acceptable.
- **`at.run()` budget:** default re-run limit is 10. The cascading `st.rerun` from button clicks in `model_card.py` could exceed it if tests chain too many actions in one `run`. Mitigation: one action per `at.run()`, assert in between.
- **`from_function(main)` vs `from_file`:** `main()` imports tab modules **inside** the `with tab:` blocks (lazy imports). AppTest must keep these reachable — confirmed, since imports happen at runtime during `tab.run()`. If this turns out flaky, fall back to a one-line entry shim.
- **`st.set_page_config` side effects:** `app.py` calls this at module top. With `from_function`, that line doesn't execute, which is fine. With `from_file`, it runs once per `AppTest` instance — also fine, but worth knowing if we switch.
- **Pandas-DataFrame assertions:** `at.dataframe[i].value` returns the underlying DataFrame. Assertions should check row count and a subset of cells, not equality on the whole frame (column ordering is locked in `_DISPLAY_COLUMNS`, so this is stable, but be defensive).
- **`render_port_picker` reuse:** if it ever grows a `st.popover`, that becomes a new gotcha class. Out of scope for #69; track as a watch item.

---

## 7. Out of scope

- Backend coverage gaps in `state.py:563-598`, `core/process.py`, `core/lockfile.py` — those stay residual Phase D.
- Browser-driven tests (Playwright/Selenium) — explicitly rejected per #69 research.
- A stdio-protocol smoke test for the Streamlit server — orthogonal to widget coverage.
- Visual regression / screenshot tests — not a goal of this harness.
- Refactoring tab modules to make them more testable. If a specific line is genuinely unreachable via AppTest (e.g. an `if False`-guarded branch), document and `# pragma: no cover` it rather than restructuring.
- Extending the harness to the `llauncher/ui/components/` subdirectory beyond what's needed to render the tabs cleanly. Component-level coverage rides on tab tests for now.

---

## Critical files for implementation

- `llauncher/ui/app.py` — entry point for `AppTest.from_function(main)`.
- `llauncher/ui/tabs/audit.py` — pilot target.
- `llauncher/ui/tabs/forms.py` — largest payoff (144 missed lines).
- `llauncher/ui/tabs/nodes.py` — second-largest; dual-submit form gotcha.
- `tests/conftest.py` — model-health autouse mock must compose with new UI fixtures.

---

## Next executable step

In an edit-capable session, implement the pilot:
1. `tests/unit/ui/__init__.py` (empty).
2. `tests/unit/ui/conftest.py` — `ui_app` fixture per §2.
3. `tests/unit/ui/_helpers.py` — `click_button_by_label`, `set_form_inputs`, `assert_toast`.
4. `tests/unit/ui/test_audit_apptest.py` — ~8 branch-coverage tests for `render_audit_tab`.

That single PR establishes the harness contract; subsequent tabs follow the rollout in §4.
