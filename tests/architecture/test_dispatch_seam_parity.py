"""Static dispatch-seam parity guard across the four endpoint surfaces.

Why this test exists
--------------------
``docs/ARCHITECTURE.md`` states the rule plainly: "Endpoints orchestrate;
orchestration uses core; core uses models." Four surfaces dispatch mutating
verbs today — ``llauncher/ui/``, ``llauncher/cli.py``, ``llauncher/mcp_server/``,
and ``llauncher/agent/`` — and the #330 audit's verb x surface parity matrix
found that three of those four gate identically through
``llauncher.core.delegation.should_delegate()`` before falling back to the
same in-process ``llauncher.operations`` verb (``agent/`` is the delegation
*target*, not a gate participant — ``core/delegation.py``'s
``is_agent_process()`` short-circuits it, #62).

This test pins the mechanical subset of that parity invariant so a future
front-end change cannot silently reopen a special case (#332's
``state.stop_server`` legacy path is the concrete precedent this guard is
named after):

1. **One door for mutating mechanics** — no endpoint module reaches past
   ``llauncher.operations`` / ``local_agent_node()`` into the sub-ops
   mutators directly.
2. **Legacy ``LauncherState`` verb methods are dead to endpoints** — a call
   to ``start_server``/``stop_server``/``start_with_eviction[_compat]`` is
   allowed only when the receiver is (traceably) a ``local_agent_node()``
   result, since ``RemoteNode`` happens to share those method names.
3. **Delegation-gate pairing** (front-end layers only — ``ui/``, ``cli.py``,
   ``mcp_server/``): a function that delegates a verb must also carry the
   in-process ``ops`` twin, and any front-end spawn verb must sit behind
   ``should_delegate()``.
4. **Caller-tag parity** — every ``ops.<verb>()`` dispatch passes an explicit
   ``caller=`` literal naming its own surface; no surface dispatches under
   the ``"unknown"`` default.

Pattern: ``tests/architecture/test_ui_layer_boundaries.py`` (AST scan,
fail-fast citing file:line, plus a planted-violation meta-test so the
scanner can never go quietly blind). Spec: issue #330 (audit) /
``issues/330#issuecomment-4997012259`` (deliverable-3 mechanical spec).

If this test FAILS, an endpoint module has reopened a dispatch-seam special
case. The fix is never to loosen this guard — route the call through
``llauncher.operations`` (or ``local_agent_node()`` under a
``should_delegate()`` gate), with an explicit ``caller=`` tag matching the
surface. See ``docs/ARCHITECTURE.md`` and issue #330.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARCH_DOC = "docs/ARCHITECTURE.md"
_ISSUE = "#330"

# The four scanned endpoint roots. "front-end" = the first three; agent/ is
# the delegation target and is exempt from the gate assertions (3).
_UI_ROOT = _REPO_ROOT / "llauncher" / "ui"
_CLI_FILE = _REPO_ROOT / "llauncher" / "cli.py"
_MCP_ROOT = _REPO_ROOT / "llauncher" / "mcp_server"
_AGENT_ROOT = _REPO_ROOT / "llauncher" / "agent"

_FRONT_END_ROOTS = ("ui", "cli", "mcp")

# Verb -> in-process ops twin (Assertion 3a's fixed map).
_DELEGATED_TO_TWIN = {
    "start_server": "start",
    "stop_server": "stop",
    "swap_server": "swap",
}
# Legacy LauncherState verb methods that must never fire on anything but a
# local_agent_node()/RemoteNode receiver (Assertion 2).
_LEGACY_VERB_NAMES = frozenset(
    {"start_server", "stop_server", "start_with_eviction", "start_with_eviction_compat"}
)
# Sub-ops mutators endpoints may never call directly (Assertion 1).
_DENIED_PROCESS_MUTATORS = frozenset(
    {"start_server", "stop_server_by_port", "stop_server_by_pid"}
)
_DENIED_LOCKFILE_MUTATORS = frozenset({"write_lockfile", "remove_lockfile"})

# Verbs exempt from the "spawn verbs must be gated" rule (Assertion 3b).
_UNGATED_OPS_VERBS = frozenset(
    {"delete_model", "list_orphans", "reconcile_stale_lockfiles"}
)
_GATED_OPS_VERBS = frozenset({"start", "stop", "swap"})

# ops.<verb>() calls that carry a caller= dispatch-attribution contract
# (Assertion 4). Coordination/read helpers with no `caller` parameter at all
# (`join_inflight_stop`, `wait_for_stop`) are deliberately excluded — they
# are not verb dispatches, so caller-tag parity does not apply to them.
_CALLER_TAGGED_OPS_VERBS = frozenset(
    {
        "start",
        "stop",
        "stop_in_background",
        "swap",
        "delete_model",
        "list_orphans",
        "reconcile_stale_lockfiles",
    }
)

# caller= surface for each front-end root; agent/ handled separately since it
# also accepts "status" (the /status sweep) and the tested "agent-shutdown"
# reap-path tag (both are surface-prefixed "agent" callers, not a stray
# surface — see tests/unit/test_agent_lifespan.py).
_EXPECTED_CALLER = {"ui": "ui", "cli": "cli", "mcp": "mcp"}


# ─────────────────────────── AST plumbing ────────────────────────────


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _iter_py_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(p for p in root.rglob("*.py"))


def _surface_for(path: Path) -> str:
    rel = path.relative_to(_REPO_ROOT).as_posix()
    if rel == "llauncher/cli.py":
        return "cli"
    if rel.startswith("llauncher/ui/"):
        return "ui"
    if rel.startswith("llauncher/mcp_server/"):
        return "mcp"
    if rel.startswith("llauncher/agent/"):
        return "agent"
    raise AssertionError(f"file outside the four scanned endpoint roots: {rel}")


def _all_endpoint_files() -> list[Path]:
    files = (
        _iter_py_files(_UI_ROOT)
        + _iter_py_files(_CLI_FILE)
        + _iter_py_files(_MCP_ROOT)
        + _iter_py_files(_AGENT_ROOT)
    )
    return sorted(set(files))


def _imported_names(tree: ast.Module) -> dict[str, tuple[str, str]]:
    """Map local-binding-name -> (module, original_name) for every import.

    Covers ``import X.Y as z`` (module='X.Y', original='X.Y') and
    ``from X import y as z`` (module='X', original='y'), so an attribute
    call through an aliased module (``proc.start_server`` where
    ``proc = llauncher.core.process``) resolves back to its source symbol.
    """
    out: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                out[local] = (alias.name, alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local = alias.asname or alias.name
                out[local] = (module, alias.name)
    return out


def _dotted_name(node: ast.AST) -> str | None:
    """Best-effort dotted-name reconstruction of a Name/Attribute chain."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


# ─────────────── Assertion 1: sub-ops mutators deny-list ───────────────


def _find_deny_list_violations(path: Path, tree: ast.Module) -> list[str]:
    violations: list[str] = []
    imported = _imported_names(tree)
    rel = path.relative_to(_REPO_ROOT)

    # (a) `from llauncher.core.process import start_server` / `... lockfile
    # import write_lockfile` style direct-symbol imports.
    for local, (module, original) in imported.items():
        if module == "llauncher.core.process" and original in _DENIED_PROCESS_MUTATORS:
            violations.append(
                f"{rel}: imports 'llauncher.core.process.{original}' directly "
                f"(bound as {local!r}). Endpoints dispatch mutations only through "
                f"llauncher.operations or local_agent_node()."
            )
        if module == "llauncher.core.lockfile" and original in _DENIED_LOCKFILE_MUTATORS:
            violations.append(
                f"{rel}: imports 'llauncher.core.lockfile.{original}' directly "
                f"(bound as {local!r}). Endpoints dispatch mutations only through "
                f"llauncher.operations or local_agent_node()."
            )

    # (b) `import llauncher.core.process as proc` then `proc.start_server(...)`
    # attribute-call access, or `from llauncher.core import process as proc`.
    module_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("llauncher.core.process", "llauncher.core.lockfile"):
                    module_aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module == "llauncher.core" or node.module == "llauncher.core.process":
                for alias in node.names:
                    if alias.name == "process":
                        module_aliases[alias.asname or "process"] = "llauncher.core.process"
                    if alias.name == "lockfile":
                        module_aliases[alias.asname or "lockfile"] = "llauncher.core.lockfile"

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        base = _dotted_name(node.func.value)
        if base in module_aliases:
            resolved_module = module_aliases[base]
            attr = node.func.attr
            if resolved_module == "llauncher.core.process" and attr in _DENIED_PROCESS_MUTATORS:
                violations.append(
                    f"{rel}:{node.lineno}: calls "
                    f"'{base}.{attr}' (llauncher.core.process.{attr}) directly. "
                    "Endpoints dispatch mutations only through llauncher.operations "
                    "or local_agent_node()."
                )
            if resolved_module == "llauncher.core.lockfile" and attr in _DENIED_LOCKFILE_MUTATORS:
                violations.append(
                    f"{rel}:{node.lineno}: calls "
                    f"'{base}.{attr}' (llauncher.core.lockfile.{attr}) directly. "
                    "Endpoints dispatch mutations only through llauncher.operations "
                    "or local_agent_node()."
                )

    return violations


def test_no_endpoint_imports_sub_ops_mutators_directly():
    """Assertion 1: no endpoint reaches past ops/local_agent_node into core mutators.

    ``llauncher.core.process.{start_server,stop_server_by_port,
    stop_server_by_pid}`` and ``llauncher.core.lockfile.{write_lockfile,
    remove_lockfile}`` are sub-ops of ``llauncher.operations`` — an endpoint
    that imports or attribute-calls them directly has reopened a private
    door under ONE-DOOR. Reads (``stream_logs``, ``read_logs_for_port``,
    ``read_lockfile``, ``reconcile_lockfile``) stay allowed.
    """
    violations: list[str] = []
    for path in _all_endpoint_files():
        tree = _parse(path)
        violations.extend(_find_deny_list_violations(path, tree))

    assert not violations, (
        "endpoint module(s) bypassed the operations door onto a core mutator "
        f"directly (see {_ARCH_DOC} 'Endpoints orchestrate' and {_ISSUE}):\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ─────────── Assertion 2: legacy LauncherState verbs dead to endpoints ───────────


def _local_agent_node_result_names(func: ast.AST) -> set[str]:
    """Names in ``func`` whose latest assignment is a ``local_agent_node()`` call."""
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            value = node.value
            is_local_agent_call = (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "local_agent_node"
            )
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if is_local_agent_call:
                        names.add(target.id)
                    else:
                        names.discard(target.id)
    return names


def _is_local_agent_node_receiver(receiver: ast.AST, local_names: set[str]) -> bool:
    # local_agent_node().start_server(...) — receiver IS the call.
    if (
        isinstance(receiver, ast.Call)
        and isinstance(receiver.func, ast.Name)
        and receiver.func.id == "local_agent_node"
    ):
        return True
    # `node = local_agent_node(); node.start_server(...)` — traced local name.
    if isinstance(receiver, ast.Name) and receiver.id in local_names:
        return True
    return False


def _module_alias_names(tree: ast.Module) -> set[str]:
    """Local names bound to an imported *module* (as opposed to an instance).

    A module-qualified call like ``servers_tools.start_server(args)`` (the
    MCP dispatch table calling the ``tools/servers.py`` wrapper *function*
    named ``start_server``) shares an attribute name with the legacy verb
    methods but is not a call on a stateful receiver at all — module aliases
    must be excluded from Assertion 2's receiver check or they read as a
    false positive.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                # Heuristic: `from pkg.tools import servers as servers_tools`
                # imports a submodule (lowercase, no re-export list needed);
                # `from pkg import Something` imports a symbol. We can't
                # always tell without resolving the filesystem, so treat any
                # `from X import Y as Z` where Y look like a module path
                # segment (all-lowercase, no leading class-case) as a
                # possible module alias — conservative in the direction of
                # not under-flagging real instance calls, since a stray
                # module-alias false-negative here would just mean
                # Assertion 2 stays silent on a shape it was never asked to
                # catch (imports are still separately gated by Assertion 1).
                local = alias.asname or alias.name
                if alias.name.islower() and "." not in alias.name:
                    names.add(local)
    return names


def _find_legacy_verb_violations(path: Path, tree: ast.Module) -> list[str]:
    violations: list[str] = []
    rel = path.relative_to(_REPO_ROOT)
    module_aliases = _module_alias_names(tree)

    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local_names = _local_agent_node_result_names(func)
        for node in ast.walk(func):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in _LEGACY_VERB_NAMES:
                continue
            if _is_local_agent_node_receiver(node.func.value, local_names):
                continue
            receiver = node.func.value
            if isinstance(receiver, ast.Name) and receiver.id in module_aliases:
                continue  # module-qualified function call, not an instance verb
            receiver_desc = ast.dump(receiver)
            violations.append(
                f"{rel}:{node.lineno}: calls '.{node.func.attr}(...)' on a "
                f"receiver that is not local_agent_node() ({receiver_desc}). "
                "Legacy LauncherState verb methods are dead to endpoints — "
                "dispatch through llauncher.operations instead."
            )

    return violations


def test_legacy_launcher_state_verbs_dead_to_endpoints():
    """Assertion 2: start_server/stop_server/start_with_eviction[_compat] gated.

    These attribute names are shared between the legacy ``LauncherState``
    verb methods and ``RemoteNode`` (the ``local_agent_node()`` delegation
    target) — only the latter is a sanctioned receiver. Anything else
    (``state.stop_server(...)``, ``LauncherState().start_server(...)``) is
    the #332 special case this assertion exists to keep dead.
    """
    violations: list[str] = []
    for path in _all_endpoint_files():
        tree = _parse(path)
        violations.extend(_find_legacy_verb_violations(path, tree))

    assert not violations, (
        "endpoint module(s) called a legacy LauncherState verb on a "
        f"non-local_agent_node() receiver (see {_ARCH_DOC} and {_ISSUE}, "
        "and the #332 fix this assertion pins):\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ─────────────── Assertion 3: delegation-gate pairing (front-end only) ───────────────


def _calls_named(func: ast.AST, name: str) -> bool:
    """True if ``func`` contains a call to ``name`` — bare (``should_delegate()``)
    or attribute-qualified (``delegation.should_delegate()``, the real
    shape used by every caller in this codebase)."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == name:
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr == name:
            return True
    return False


def _delegated_verbs_present(func: ast.AST) -> set[str]:
    """Delegated verb names (start_server/stop_server/swap_server) called on
    a local_agent_node() receiver anywhere inside ``func``."""
    found: set[str] = set()
    local_names = _local_agent_node_result_names(func)
    for node in ast.walk(func):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _DELEGATED_TO_TWIN:
            continue
        if _is_local_agent_node_receiver(node.func.value, local_names):
            found.add(node.func.attr)
    return found


def _ops_verbs_present(func: ast.AST) -> set[str]:
    """ops.<verb>()/operations.<verb>() calls (by verb name) inside ``func``."""
    found: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        base = _dotted_name(node.func.value)
        if base in ("ops", "operations"):
            found.add(node.func.attr)
    return found


def _find_gate_pairing_violations(path: Path, tree: ast.Module) -> list[str]:
    violations: list[str] = []
    rel = path.relative_to(_REPO_ROOT)

    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        delegates = _calls_named(func, "should_delegate")
        delegated_verbs = _delegated_verbs_present(func)
        ops_verbs = _ops_verbs_present(func)

        # (a) delegated => twin present.
        if delegates and delegated_verbs:
            for verb in delegated_verbs:
                twin = _DELEGATED_TO_TWIN[verb]
                if twin not in ops_verbs:
                    violations.append(
                        f"{rel}:{func.lineno}: function {func.name!r} delegates "
                        f"'{verb}' via local_agent_node() but has no in-process "
                        f"'ops.{twin}(...)' twin in the same function."
                    )

        # (b) spawn verbs gated.
        gated_calls = ops_verbs & _GATED_OPS_VERBS
        if gated_calls and not delegates:
            for verb in sorted(gated_calls):
                violations.append(
                    f"{rel}:{func.lineno}: function {func.name!r} calls "
                    f"'ops.{verb}(...)' without also calling should_delegate() — "
                    "front-end spawn verbs must sit behind the delegation gate."
                )

    return violations


def test_frontend_delegation_gate_pairing():
    """Assertion 3: delegated verbs pair with their ops twin; spawns are gated.

    Front-end only (``ui/``, ``cli.py``, ``mcp_server/`` — ``agent/`` is the
    delegation target and is exempt). (a) A function that delegates a verb
    via ``local_agent_node()`` must also carry the mapped in-process
    ``ops.<verb>`` twin — this independently catches the #332 shape (a
    delegated stop paired with a non-ops fallback). (b) Any front-end
    ``ops.start``/``ops.stop``/``ops.swap`` call must sit in a function that
    also calls ``should_delegate()`` (pins #200/#194 sole-spawner). Exempt:
    ``ops.delete_model``, ``ops.list_orphans``,
    ``ops.reconcile_stale_lockfiles`` (ratified ungated), and
    ``core.marker.request_cancel`` (ADR-014, uniformly core-direct — not an
    ``ops.`` verb at all, so it never enters this scan).
    """
    violations: list[str] = []
    for root_name, root in (
        ("ui", _UI_ROOT),
        ("cli", _CLI_FILE),
        ("mcp", _MCP_ROOT),
    ):
        assert root_name in _FRONT_END_ROOTS
        for path in _iter_py_files(root):
            tree = _parse(path)
            violations.extend(_find_gate_pairing_violations(path, tree))

    assert not violations, (
        "front-end delegation-gate pairing broken (see "
        f"{_ARCH_DOC} and {_ISSUE}):\n" + "\n".join(f"  {v}" for v in violations)
    )


# ───────────────────── Assertion 4: caller-tag parity ─────────────────────


def _keyword_str_value(call: ast.Call, kw_name: str) -> tuple[bool, str | None]:
    """Return (has_keyword, static_str_or_None) for a keyword on ``call``."""
    for kw in call.keywords:
        if kw.arg == kw_name:
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return True, kw.value.value
            return True, None  # present but not a static string literal
    return False, None


def _cli_caller_param_defaults(tree: ast.Module) -> set[str]:
    """Local parameter names in cli.py whose typer default is "cli".

    Backs the "Typer-param indirection" allowance: `caller: str =
    typer.Option("cli", hidden=True)` then `caller=caller` downstream.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for arg, default in zip(
            node.args.args[-len(node.args.defaults):] if node.args.defaults else [],
            node.args.defaults,
        ):
            if (
                arg.arg == "caller"
                and isinstance(default, ast.Call)
                and isinstance(default.func, ast.Attribute)
                and default.func.attr == "Option"
                and default.args
                and isinstance(default.args[0], ast.Constant)
                and default.args[0].value == "cli"
            ):
                names.add(arg.arg)
    return names


def _find_caller_tag_violations(path: Path, tree: ast.Module) -> list[str]:
    violations: list[str] = []
    rel = path.relative_to(_REPO_ROOT)
    surface = _surface_for(path)

    cli_indirect_names = _cli_caller_param_defaults(tree) if surface == "cli" else set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        base = _dotted_name(node.func.value)
        if base not in ("ops", "operations"):
            continue
        verb = node.func.attr
        if verb not in _CALLER_TAGGED_OPS_VERBS:
            # e.g. join_inflight_stop/wait_for_stop: coordination reads with
            # no `caller` parameter at all — not a verb dispatch.
            continue

        has_kw, static_value = _keyword_str_value(node, "caller")
        if not has_kw:
            violations.append(
                f"{rel}:{node.lineno}: ops.{verb}(...) has no caller= keyword "
                "(dispatches under the 'unknown' default)."
            )
            continue

        if static_value is None:
            # Non-literal caller= value — must be the Typer-param indirection
            # (`caller=caller` where `caller` param defaults to "cli").
            kw_node = next(kw for kw in node.keywords if kw.arg == "caller")
            if (
                surface == "cli"
                and isinstance(kw_node.value, ast.Name)
                and kw_node.value.id in cli_indirect_names
            ):
                continue
            violations.append(
                f"{rel}:{node.lineno}: ops.{verb}(caller=...) is not a static "
                "string literal and is not the recognized cli Typer-param "
                "indirection — caller-tag parity must be statically verifiable."
            )
            continue

        if surface == "agent":
            # Agent accepts "agent" (the ordinary verb endpoints), "status"
            # (the /status reconcile sweep), and "agent-shutdown" (the
            # lifespan-reap path — its own surface-prefixed tag, pinned by
            # tests/unit/test_agent_lifespan.py).
            if static_value == "agent" or static_value == "status" or (
                static_value.startswith("agent-")
            ):
                continue
            violations.append(
                f"{rel}:{node.lineno}: ops.{verb}(caller={static_value!r}) does "
                "not match the agent surface ('agent', 'agent-<subpath>', or "
                "'status' for the sweep)."
            )
            continue

        expected = _EXPECTED_CALLER[surface]
        if static_value != expected:
            violations.append(
                f"{rel}:{node.lineno}: ops.{verb}(caller={static_value!r}) does "
                f"not match its surface (expected {expected!r})."
            )

    return violations


def test_caller_tag_parity():
    """Assertion 4: every ops.<verb>() dispatch tags its own surface.

    ``caller=`` is part of the dispatch contract (audit-trail attribution),
    not decoration — a verb fired with the ``"unknown"`` default, or another
    surface's tag, fails here. The CLI's ``caller=caller`` Typer-param
    indirection (default ``"cli"``) is accepted since it is statically
    traceable to the literal; the agent's ``"status"`` sweep tag and its
    ``"agent-shutdown"`` lifespan-reap tag are both surface-owned
    (tests/unit/test_agent_lifespan.py pins the latter) and accepted.
    """
    violations: list[str] = []
    for path in _all_endpoint_files():
        tree = _parse(path)
        violations.extend(_find_caller_tag_violations(path, tree))

    assert not violations, (
        f"caller-tag parity broken (see {_ARCH_DOC} and {_ISSUE}):\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ───────────────────────────── Meta-test ─────────────────────────────


def _tree_for(source: str) -> ast.Module:
    return ast.parse(source)


def test_guard_actually_detects_planted_violations():
    """Meta-test: the four assertions' classifiers must catch known-bad shapes.

    Mirrors ``test_guard_actually_detects_a_planted_violation`` in
    ``test_ui_layer_boundaries.py``: feed each classifier synthetic snippets
    shaped like the violations the audit named, and assert every one is
    caught — plus the named legitimate shapes are NOT flagged, so a future
    refactor that neuters a classifier fails here instead of going quietly
    blind.
    """
    fake_path = _REPO_ROOT / "llauncher" / "ui" / "tabs" / "_planted.py"

    # --- must be CAUGHT ---------------------------------------------------

    # 1. state.stop_server(...) in a ui module (the #332 shape).
    planted_state_stop = """
def _handle_stop(state, port):
    success, message = state.stop_server(port, caller="ui")
"""
    tree = _tree_for(planted_state_stop)
    violations = _find_legacy_verb_violations(fake_path, tree)
    assert violations, "classifier failed to catch state.stop_server(...) in ui"

    # 2. delegated swap with no ops.swap twin.
    planted_missing_twin = """
def _render_eviction_dialog(model_name, port):
    if delegation.should_delegate():
        res = local_agent_node().swap_server(model_name, port) or {}
    else:
        pass
"""
    tree = _tree_for(planted_missing_twin)
    violations = _find_gate_pairing_violations(fake_path, tree)
    assert violations, "classifier failed to catch delegated-swap-without-twin"

    # 3. ungated ops.start in an mcp module.
    planted_ungated_start = """
def start_server(model_name, port):
    result = ops.start(model_name, port, caller="mcp")
"""
    tree = _tree_for(planted_ungated_start)
    violations = _find_gate_pairing_violations(fake_path, tree)
    assert violations, "classifier failed to catch ungated ops.start in mcp"

    # 4. caller="unknown" default dispatch.
    planted_unknown_caller = """
def start_server(model_name, port):
    result = ops.start(model_name, port, caller="unknown")
"""
    tree = _tree_for(planted_unknown_caller)
    fake_ui_path = _REPO_ROOT / "llauncher" / "ui" / "tabs" / "_planted.py"
    violations = _find_caller_tag_violations(fake_ui_path, tree)
    assert violations, "classifier failed to catch caller='unknown' in ui"

    # 5. missing caller= entirely.
    planted_no_caller = """
def start_server(model_name, port):
    result = ops.start(model_name, port)
"""
    tree = _tree_for(planted_no_caller)
    violations = _find_caller_tag_violations(fake_ui_path, tree)
    assert violations, "classifier failed to catch missing caller= in ui"

    # 6. direct sub-ops mutator import/call.
    planted_direct_mutator = """
from llauncher.core import process as proc

def stop_server(port):
    proc.stop_server_by_port(port)
"""
    tree = _tree_for(planted_direct_mutator)
    violations = _find_deny_list_violations(fake_path, tree)
    assert violations, "classifier failed to catch direct core.process mutator call"

    # --- must be ALLOWED (no false positives) ------------------------------

    # a. ungated ops.delete_model (ratified ungated on all 4 surfaces).
    allowed_delete = """
def delete_model_handler(name):
    result = ops.delete_model(name, caller="ui")
"""
    tree = _tree_for(allowed_delete)
    assert not _find_gate_pairing_violations(fake_path, tree), (
        "false positive: ops.delete_model flagged as an ungated spawn"
    )

    # b. agent-layer ops.start(caller="agent") — agent is exempt from the
    # gate assertions, and "agent" is the correct caller tag.
    allowed_agent_start = """
def start_server(port, body):
    result = ops.start(body.model, port, caller="agent")
"""
    tree = _tree_for(allowed_agent_start)
    fake_agent_path = _REPO_ROOT / "llauncher" / "agent" / "routing.py"
    assert not _find_caller_tag_violations(fake_agent_path, tree), (
        "false positive: agent-layer ops.start(caller='agent') flagged"
    )
    # Not scanned by Assertion 3 at all since agent/ is excluded from that
    # test's root list — assert the exemption holds structurally too.
    assert "agent" not in _FRONT_END_ROOTS

    # c. local_agent_node().stop_server(...) — the sanctioned receiver.
    allowed_delegate_stop = """
def _handle_stop(port):
    res = local_agent_node().stop_server(port) or {}
"""
    tree = _tree_for(allowed_delegate_stop)
    assert not _find_legacy_verb_violations(fake_path, tree), (
        "false positive: local_agent_node().stop_server(...) flagged"
    )

    # d. delegated verb correctly paired with its ops twin (the real
    # model_card.py shape) — must NOT be flagged.
    allowed_paired = """
def _handle_stop(state, port):
    if delegation.should_delegate():
        res = local_agent_node().stop_server(port) or {}
    else:
        result = ops.stop(port, caller="ui")
"""
    tree = _tree_for(allowed_paired)
    assert not _find_gate_pairing_violations(fake_path, tree), (
        "false positive: correctly paired delegated stop + ops.stop twin flagged"
    )
