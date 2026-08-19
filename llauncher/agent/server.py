"""FastAPI server for the llauncher agent service.

This agent wraps the local LauncherState and exposes it over HTTP,
allowing a head dashboard to manage this node remotely.

Usage:
    llauncher-agent
    # or with custom config
    LLAUNCHER_AGENT_PORT=9000 LLAUNCHER_AGENT_NODE_NAME="my-node" llauncher-agent
    # stop running agent
    llauncher-agent --stop
"""

import argparse
import copy
import logging
import os
import signal
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from llauncher import __version__
from llauncher import operations as ops
from llauncher.agent.auth import (
    count_env_file_token_lines,
    default_env_path,
    is_loopback,
    legacy_token_env_misconfigured,
    resolve_agent_token,
)
from llauncher.agent.config import AgentConfig
from llauncher.agent.middleware import (
    AuthenticationMiddleware,
    BodySizeLimitMiddleware,
)
from llauncher.agent.routing import router, get_node_name
from llauncher.core import delegation
from llauncher.core import lockfile as lf
from llauncher.core import settings
from llauncher.core.settings import AGENT_API_KEY, LAUNCHER_LOG_DIR

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def _configure_logging() -> None:
    """Configure agent-process logging (issue #128).

    Under NSSM (Windows service manager) — and any non-TTY supervisor —
    ``sys.stdout``/``sys.stderr`` are block-buffered (typically 8 KB),
    not line-buffered, so ``logging``'s default ``StreamHandler`` output
    sits in the buffer indefinitely instead of reaching the redirected
    log files. Reconfiguring both streams to line-buffering (Python 3.7+
    ``TextIOWrapper.reconfigure``) closes that gap without requiring the
    supervisor to set ``PYTHONUNBUFFERED`` itself.

    A second, independent gap: a StreamHandler alone depends entirely on
    the *supervisor* capturing and persisting stderr somewhere durable.
    Adding a ``FileHandler`` targeting ``LAUNCHER_LOG_DIR / "agent.log"``
    gives the agent its own durable log file regardless of what (if
    anything) the supervisor does with stdout/stderr — the same posture
    ``core.process`` already gives every managed ``llama-server`` child.

    Deliberately NOT called at bare module import (contrast the
    pre-#128 ``logging.basicConfig(...)`` that used to sit at import
    time): touching the filesystem (``mkdir`` + opening a
    ``FileHandler``) as an import side effect would run on every test
    collection and every process that merely imports this module
    (``mcp_server``, ``cli``, test suites), silently creating
    ``LAUNCHER_LOG_DIR`` under the real ``$HOME`` regardless of test
    isolation — the same class of defect issue #195 fixed for
    ``LLAMA_SERVER_PATH``. Instead this is invoked once, explicitly,
    from :func:`run_agent` — the actual production entry point —
    before ``uvicorn.run`` starts serving.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)

    LAUNCHER_LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(
        LAUNCHER_LOG_DIR / "agent.log", encoding="utf-8"
    )
    stream_handler = logging.StreamHandler()

    logging.basicConfig(
        level=logging.INFO,
        format=_LOG_FORMAT,
        handlers=[stream_handler, file_handler],
        force=True,
    )


# Import-time logging config: level/format only, no filesystem I/O. The
# full configuration (line-buffering reconfigure + FileHandler under
# LAUNCHER_LOG_DIR, issue #128) happens in :func:`run_agent` — see
# :func:`_configure_logging`'s docstring for why it is not called here.
logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FORMAT,
)
logger = logging.getLogger(__name__)

# uvicorn's own default log config (uvicorn.config.LOGGING_CONFIG) formats
# both the "default" (error/lifecycle) and "access" loggers WITHOUT a
# timestamp — e.g. ``INFO:     127.0.0.1:64557 - "POST /start/8081..."``.
# That makes it impossible to correlate request timing against the agent's
# own (timestamped) log lines when diagnosing hangs/slow requests (#307).
#
# This is uvicorn's stock dict config with ``%(asctime)s`` prepended to both
# formatters' ``fmt`` strings — everything else (handlers, stream targets,
# logger levels/propagation) is left exactly as uvicorn ships it, so we pick
# up upstream defaults/behavior otherwise unchanged.
UVICORN_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(asctime)s - %(levelprefix)s %(message)s",
            "use_colors": None,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": (
                "%(asctime)s - %(levelprefix)s %(client_addr)s - "
                '"%(request_line)s" %(status_code)s'
            ),
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
        "access": {
            "formatter": "access",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
    },
}


def _build_uvicorn_log_config(log_dir: Path) -> dict:
    """Build a per-call uvicorn ``log_config`` with agent.log wired in.

    Verified defect: ``UVICORN_LOG_CONFIG`` sets ``propagate: False`` on the
    ``uvicorn`` and ``uvicorn.access`` loggers with stream-only handlers, so
    none of uvicorn's request/access/error traffic ever reaches the root
    logger's ``FileHandler`` that :func:`_configure_logging` attaches — it
    only sees the three startup lines this module itself logs. Under a
    non-TTY supervisor (NSSM, issue #128's original case) that means
    ``agent.log`` is missing exactly the traffic an operator needs when
    diagnosing a hang or an error after the fact.

    This deep-copies the module-level ``UVICORN_LOG_CONFIG`` template and
    adds two ``FileHandler`` entries — one per existing formatter, so file
    lines carry the same timestamped format as their stream counterparts —
    targeting ``log_dir / "agent.log"``, then appends them to the
    ``uvicorn`` and ``uvicorn.access`` loggers' handler lists alongside the
    existing stream handlers (stdout/stderr behavior is unchanged; nothing
    is removed). ``uvicorn.error`` needs no separate entry: it has no
    handlers of its own and propagates (default) up to ``uvicorn``, so it
    rides the same "default" file handler.

    Built fresh on every call — never at import time — so the resolved
    path always reflects the current ``LAUNCHER_LOG_DIR`` (env/test
    overrides included) rather than whatever it was when this module was
    first imported. Mirrors the runtime-only-filesystem-work discipline
    :func:`_configure_logging`'s docstring documents for issue #195; the
    module-level ``UVICORN_LOG_CONFIG`` itself stays a pure in-memory
    template with no filesystem I/O of its own.
    """
    config = copy.deepcopy(UVICORN_LOG_CONFIG)
    log_path = str(Path(log_dir) / "agent.log")

    config["handlers"]["default_file"] = {
        "formatter": "default",
        "class": "logging.FileHandler",
        "filename": log_path,
        "encoding": "utf-8",
    }
    config["handlers"]["access_file"] = {
        "formatter": "access",
        "class": "logging.FileHandler",
        "filename": log_path,
        "encoding": "utf-8",
    }
    config["loggers"]["uvicorn"]["handlers"].append("default_file")
    config["loggers"]["uvicorn.access"]["handlers"].append("access_file")
    return config


def find_process_on_port(port: int) -> int | None:
    """Find the PID of the process listening on the given port.

    Args:
        port: Port number to check.

    Returns:
        PID of the process, or None if not found.
    """

    # Try to find process using /proc on Linux
    if sys.platform == "linux":
        import glob

        for fd_path in glob.glob(f"/proc/*/fd/*"):
            try:
                fd = int(fd_path.split("/")[-1])
                link = os.readlink(fd_path)
                if "socket:" in link:
                    # Get the process ID
                    pid = int(fd_path.split("/")[2])
                    # Check if this socket is bound to our port
                    # by reading /proc/net/tcp
                    with open("/proc/net/tcp") as f:
                        for line in f:
                            if ":%.4X " % port in line:
                                return pid
            except (ValueError, OSError, FileNotFoundError):
                continue
        return None

    # On Windows, we'd need to use netstat or wmi
    # For now, just return None and let the caller handle it
    return None


def stop_agent(port: int) -> bool:
    """Stop any agent running on the given port.

    Args:
        port: Port the agent is listening on.

    Returns:
        True if agent was stopped, False if no agent found or error.
    """
    try:
        # Try to connect to the agent's health endpoint
        import httpx

        response = httpx.get(f"http://localhost:{port}/health", timeout=2.0)
        if response.status_code == 200:
            # Agent is running, try to find and kill it
            pid = find_process_on_port(port)
            if pid:
                os.kill(pid, signal.SIGTERM)
                logger.info(f"Agent (PID {pid}) terminated")
                return True

            # Fallback: try to find via socket
            import psutil

            for conn in psutil.net_connections(kind="tcp"):
                if conn.laddr.port == port and conn.status == "LISTEN":
                    try:
                        proc = conn.pid
                        p = psutil.Process(proc)
                        p.terminate()
                        logger.info(f"Agent (PID {proc}) terminated")
                        return True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

            logger.warning("Agent is running but could not find process to terminate")
            return False
        else:
            logger.info("No agent responding on port")
            return False
    except httpx.RequestError:
        logger.info("No agent running on port")
        return False
    except Exception as e:
        logger.error(f"Error stopping agent: {e}")
        return False


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan: reap llauncher-managed llama-server children on shutdown.

    Per Issue #65 (Phase 2 of the v2 phased plan): uvicorn 0.35 captures both
    SIGTERM and SIGINT and drains in-flight HTTP requests, but the agent does
    not reap the llama-server children it spawned because those processes are
    started with ``start_new_session=True`` (own process group) and therefore
    do not receive the agent's signals transitively.

    On shutdown we enumerate the per-port lockfile registry — the durable
    record of llauncher-managed children — and dispatch each through the
    existing :func:`operations.stop` verb, which handles audit emission,
    psutil-based termination, and lockfile removal.

    Symmetric on SIGTERM and SIGINT: any agent shutdown reaps children. This
    is a behavior change from the previous bare ``KeyboardInterrupt`` path,
    which orphaned children silently. See ``docs/v2-handoff.md`` §What NOT
    To Do for context.

    Coalescing with in-flight background stops (issue #140): a
    ``POST /stop/{port}`` accepted shortly before shutdown runs its
    termination on a daemon thread. For each port the reaper first joins
    any such in-flight stop (:func:`operations.join_inflight_stop`,
    bounded by the full SIGTERM grace budget) and skips its own blocking
    stop when the in-flight one completed — otherwise two threads would
    race SIGTERM/SIGKILL against the same process. Remaining tolerance:
    a background stop registered after the join, or a stop driven from
    another process, can still overlap the blocking call; that overlap
    is at worst a re-termination of an already-dying pid, which
    ``core.process.stop_server_by_pid`` absorbs.
    """
    # Startup — self-provision the run/ directory (issue #201 Part 1).
    # Lockfile and marker writes target {LAUNCHER_RUN_DIR}/{port}.lock|.swap
    # with O_EXCL. After a fresh system-mode install the migrated state dir
    # carries logs/ and audit.jsonl but intentionally not run/, so the first
    # launch could fail before llama-server even spawns. Create it eagerly
    # here, mirroring the LOG_DIR.mkdir in core.process.start_server. A
    # PermissionError on a read-only state dir is a real misconfiguration and
    # is intentionally allowed to surface.
    settings.LAUNCHER_RUN_DIR.mkdir(parents=True, exist_ok=True)
    yield

    # Shutdown — reap managed children. We catch OSError specifically because
    # the lockfile directory may be missing or unreadable; we do NOT use a
    # bare ``except Exception`` here per the ADR-tightened convention from
    # #61. Per-port failures are logged and skipped so one bad lockfile
    # cannot abort the reap loop.
    try:
        lockfiles = lf.list_lockfiles()
    except OSError as exc:
        logger.error("Lifespan shutdown: cannot enumerate lockfiles: %s", exc)
        return

    if not lockfiles:
        logger.info("Lifespan shutdown: no managed llama-server children to reap")
        return

    logger.info(
        "Lifespan shutdown: reaping %d managed llama-server child(ren)",
        len(lockfiles),
    )
    # Full SIGTERM→SIGKILL grace budget; read at call time so env
    # profiles and test patches take effect (same discipline as
    # core.process.stop_server_by_pid).
    grace_budget = (
        settings.LLAUNCHER_STOP_CHILD_GRACE_S + settings.LLAUNCHER_STOP_GRACE_S
    )
    for entry in lockfiles:
        if ops.join_inflight_stop(entry.port, timeout=grace_budget):
            # An in-flight background stop owned this port and finished;
            # lockfile removal and audit emission already happened on
            # its thread. Driving the blocking stop too would double-
            # terminate.
            logger.info(
                "Lifespan shutdown: port=%d coalesced with in-flight stop",
                entry.port,
            )
            continue
        try:
            result = ops.stop(entry.port, caller="agent-shutdown")
        except OSError as exc:
            logger.error(
                "Lifespan shutdown: stop(port=%d) failed: %s", entry.port, exc
            )
            continue
        logger.info(
            "Lifespan shutdown: port=%d action=%s model=%s",
            entry.port,
            result.action,
            result.model,
        )


# SECURITY (§3 C1 / issue #87): ``create_app`` requires a non-empty
# ``auth_token`` so no caller can silently construct an unauthenticated
# app. The production entry point (``run_agent`` below) resolves a token
# via env / stdin / file / auto-generate before reaching this function;
# test-only construction without auth must go through
# ``create_app_unauthenticated`` so the no-auth path is grep-able and
# never reachable from production code paths.
def create_app(auth_token: str) -> FastAPI:
    """Create and configure the FastAPI application with auth enforced.

    **No CORS by design** (security plan §3 control C4): this app
    intentionally registers no CORS middleware and emits no
    ``Access-Control-*`` response headers. Browsers therefore cannot
    make cross-origin requests to the agent from arbitrary pages — a
    drive-by from any website is blocked by the same-origin policy
    before it ever reaches the auth layer. Do not add
    ``CORSMiddleware`` without first revisiting the threat model;
    ``tests/integration/test_agent_cors.py`` pins this posture as a
    regression guard (plan §4 assertion C4-a).

    Args:
        auth_token: Non-empty token to enforce on incoming requests via
            the ``X-Api-Key`` header. Passing ``None``, an empty string,
            or a whitespace-only string raises ``ValueError`` — callers
            that genuinely want an unauthenticated app must use
            :func:`create_app_unauthenticated`.

    Raises:
        ValueError: If ``auth_token`` is ``None``, empty, or whitespace
            only. A whitespace-only value indicates a malformed env-var
            assignment (e.g., ``LLAUNCHER_AGENT_TOKEN=" "``) that would
            otherwise construct an app whose auth middleware compared
            ``X-Api-Key`` against a whitespace string — see issue #111.
    """
    if not auth_token or not auth_token.strip():
        raise ValueError(
            "create_app requires a non-empty, non-whitespace auth_token. "
            "Use create_app_unauthenticated() for test-only no-auth construction."
        )
    return _build_app(auth_token=auth_token)


def create_app_unauthenticated() -> FastAPI:
    """Construct a FastAPI app with NO authentication middleware.

    SECURITY: This is a **test-only** constructor. Production code paths
    (``run_agent``) must use :func:`create_app` with a resolved token.
    The C1 invariant — "no non-loopback bind without an auth token" — is
    enforced at the ``run_agent`` callsite, not here, so this helper
    must never be reached from a production entry point.

    The ``__debug__`` guard below refuses to construct when the
    interpreter is running in optimized mode (``python -O``), which is
    the conventional posture for production deployment. In dev/test
    (``__debug__`` is ``True``) the guard is a no-op. This is a real
    runtime check — not an ``assert`` — because ``python -O`` strips
    ``assert`` statements at compile time, so an ``assert __debug__``
    would become a no-op under exactly the configuration it was meant
    to guard against (see issue #112).
    """
    # Runtime tripwire: __debug__ is False under `python -O`. We do not
    # use `assert __debug__` because -O would strip the assert itself.
    if not __debug__:
        raise RuntimeError(
            "create_app_unauthenticated() must not be reached in "
            "optimized (production) builds — use create_app(auth_token=...) "
            "instead. The C1 invariant forbids no-auth construction in any "
            "code path that could be reached from a production entry point."
        )
    return _build_app(auth_token=None)


async def _log_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Log the full traceback for any exception FastAPI itself didn't handle.

    Issue #404: an unhandled exception in a route becomes a bare 500 via
    Starlette's ``ServerErrorMiddleware`` — no traceback, no detail, in any
    log. Registering this as the app's catch-all ``Exception`` handler
    (FastAPI dispatches by most-specific exception type, so ``HTTPException``
    — the structured-error path every verb route already uses — is handled
    by FastAPI's own default handler and never reaches here, see
    ``routing.py``'s ``_start_status_code`` et al.) runs it *before*
    ``ServerErrorMiddleware`` ever sees the exception, so this is the last
    point in the app that has the real traceback.

    Logs at ERROR with ``exc_info`` (full traceback via ``logger.exception``)
    plus the route path, method, and path/query params — safe to log since
    verb routes carry only model names and ports, never secrets or prompt
    text. The response body mirrors ``ops`` verb results' structured-error
    shape (``success``/``action``/``message``) so a caller sees the same
    envelope regardless of whether the 500 came from a route's own
    try/except or from here.
    """
    logger.exception(
        "Unhandled exception in %s %s (path_params=%s, query_params=%s)",
        request.method,
        request.url.path,
        dict(request.path_params),
        dict(request.query_params),
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "action": "error",
            "message": "Unhandled exception; see agent logs.",
        },
    )


def _build_app(auth_token: str | None) -> FastAPI:
    """Internal shared builder. See ``create_app`` / ``create_app_unauthenticated``."""
    auth_active = auth_token is not None

    # Disable OpenAPI docs endpoints when authentication is configured
    docs_url = None if auth_active else "/docs"
    redoc_url = None if auth_active else "/redoc"

    app = FastAPI(
        title="llauncher Agent",
        description="Remote management agent for llauncher nodes",
        version=__version__,
        openapi_url=None if auth_active else "/openapi.json",
        docs_url=docs_url,
        redoc_url=redoc_url,
        lifespan=lifespan,
    )

    # Catch-all exception logging (issue #404): must be registered before
    # any middleware is added so it sees exceptions from every route,
    # including ones raised inside the middleware-wrapped stack below.
    app.add_exception_handler(Exception, _log_unhandled_exception)

    if auth_active:
        # Add authentication middleware when API key is configured
        app.add_middleware(AuthenticationMiddleware, expected_token=auth_token)

    # Body-size cap (security plan §3 C3 / issue #78). Registered last so
    # it becomes the outermost layer: oversize requests are rejected with
    # 413 before auth (or anything else) even sees the body, bounding the
    # worst-case memory pressure from a malicious or buggy client.
    app.add_middleware(BodySizeLimitMiddleware)

    # Include the router
    app.include_router(router, tags=["llauncher"])

    return app


def run_agent(config: AgentConfig) -> None:
    """Run the agent server.

    Enforces the security hardening §3 C1 guard: refuses to start
    when binding to a non-loopback interface without an authentication
    token configured. Auto-generates a token and persists it into
    ``~/.llauncher/agent.env`` on the first loopback start with no
    env-provided token (issue #284 — single live source, no separate
    token-mirror file).

    Also enforces a pre-#139 legacy-env guard (#281, defense in depth
    for deployments that bypass the installers' own migration): refuses
    to start when the pre-rename ``LAUNCHER_AGENT_TOKEN`` is set but the
    current ``LLAUNCHER_AGENT_TOKEN`` is not, since that combination only
    ever means a stale env file whose token nothing reads any more — the
    agent would otherwise silently fall through to the token-file /
    auto-generate path and mint a token the operator never configured.

    Args:
        config: Agent configuration.

    Raises:
        SystemExit: With code 2 when binding non-loopback without an
            available authentication token, when a pre-#139 legacy
            env var is the only token source present, or when the live
            env file carries more than one LLAUNCHER_AGENT_TOKEN= line
            (the #293 duplicate-token split-brain). The error message
            names the remediation paths.
    """
    # Full logging setup (issue #128): line-buffered stdout/stderr +
    # durable FileHandler under LAUNCHER_LOG_DIR. Done first so every
    # subsequent line in this function — including the fail-loud exits
    # below — reaches agent.log, not just what a supervisor happens to
    # capture from stdout/stderr.
    _configure_logging()

    if legacy_token_env_misconfigured():
        sys.stderr.write(
            "[llauncher-agent] ERROR: found legacy LAUNCHER_AGENT_TOKEN in "
            "the environment but no LLAUNCHER_AGENT_TOKEN.\n"
            "[llauncher-agent] Commit 9f098d9 (#138/#139) renamed "
            "LAUNCHER_AGENT_* env vars to LLAUNCHER_AGENT_* (double-L); "
            "nothing reads the old name any more, and starting anyway "
            "would silently auto-generate a different token than the one "
            "you configured (#281).\n"
            "[llauncher-agent] Remediation: rename LAUNCHER_AGENT_* keys "
            "to LLAUNCHER_AGENT_* in your env file, or re-run the "
            "installer (install.ps1 / install.sh) to migrate them "
            "automatically.\n"
        )
        raise SystemExit(2)

    # Fail loud on a duplicate token line in the live env file (#293). All
    # resolvers are last-wins (#284/d5f83b9) so a duplicate does not change
    # which value wins *now*, but two token lines in one file is the
    # split-brain footgun that reopened the UI-403 recurrence — a later
    # hand-edit that reorders them makes server and client resolve different
    # values. Refuse to run with the latent hazard rather than paper over
    # it; the remediation is to leave exactly one canonical line.
    #
    # count_env_file_token_lines only counts CANONICAL (double-L)
    # LLAUNCHER_AGENT_TOKEN= lines, so this guard only ever fires on two
    # canonical lines — never on a legacy/canonical pair (that case is
    # handled, and remediated by re-running the installer, above and by
    # #285's installer-side dedupe). Re-running the installer cannot fix
    # two canonical lines: the installer's migration only touches legacy
    # `LAUNCHER_AGENT_*` lines and leaves canonical ones untouched (#298).
    # The only remediation here is a hand-edit.
    env_path = default_env_path()
    token_line_count = count_env_file_token_lines(env_path)
    if token_line_count > 1:
        sys.stderr.write(
            "[llauncher-agent] ERROR: found "
            f"{token_line_count} LLAUNCHER_AGENT_TOKEN= lines in {env_path}.\n"
            "[llauncher-agent] Duplicate token lines are the split-brain "
            "footgun behind the recurring UI-403s (#293): a later edit that "
            "reorders them makes the agent and the UI resolve different "
            "tokens.\n"
            "[llauncher-agent] Remediation: these are canonical "
            "LLAUNCHER_AGENT_TOKEN= lines, so re-running the installer will "
            "not fix this (it only migrates legacy LAUNCHER_AGENT_* lines) "
            f"— hand-edit {env_path} to leave exactly one "
            "LLAUNCHER_AGENT_TOKEN= line.\n"
        )
        raise SystemExit(2)

    env_token = os.environ.get("LLAUNCHER_AGENT_TOKEN")
    loopback = is_loopback(config.host)

    if not loopback:
        # Non-loopback bind: require a token from env, stdin, or the
        # token file. Do NOT auto-generate in this branch — auto-gen
        # is only safe for loopback (a freshly-generated secret that
        # nobody outside the host has seen is meaningless for LAN
        # exposure).
        token = resolve_agent_token(env_value=env_token, allow_generate=False)
        if token is None:
            sys.stderr.write(
                "[llauncher-agent] ERROR: refusing to bind to non-loopback host "
                f"{config.host!r} without an authentication token.\n"
                "[llauncher-agent] Set LLAUNCHER_AGENT_TOKEN (or use "
                "LLAUNCHER_AGENT_TOKEN=- to pipe a token on stdin), or bind "
                "to 127.0.0.1 to allow auto-generation of a local token.\n"
            )
            raise SystemExit(2)
    else:
        # Loopback bind: env > stdin > file > generate-and-write.
        token = resolve_agent_token(env_value=env_token, allow_generate=True)

    app = create_app(auth_token=token)

    # Log startup info
    node_name = config.node_name or socket.gethostname()
    logger.info(f"Starting llauncher agent on {config.host}:{config.port}")
    logger.info(f"Node name: {node_name}")

    # Token is always present at this point: the non-loopback branch
    # would have exited above, and the loopback branch generates a
    # token on first run if none was supplied.
    logger.info("Authentication is active. Binding to %s", config.host)

    # Agent-identity stamp (issue #200). Set BEFORE uvicorn.run so any code
    # reached from within this process — including the in-process operations
    # the agent's own routes invoke — detects "I am the agent" via
    # ``core.delegation.is_agent_process()`` and never delegates back to
    # itself. Front-end processes (MCP, UI) never set this stamp.
    os.environ[delegation.AGENT_PROCESS_ENV] = "1"

    # Run the server. ``lifespan="on"`` ensures the FastAPI lifespan handler
    # (which reaps llama-server children on shutdown per #65) actually fires
    # regardless of uvicorn's auto-detection heuristics. ``log_config``
    # carries our timestamped access/error formatters (#307) AND, via
    # :func:`_build_uvicorn_log_config`, a FileHandler pair targeting the
    # same ``agent.log`` that :func:`_configure_logging` set up above —
    # otherwise uvicorn's own loggers (propagate=False, stream-only
    # handlers) never reach the durable file at all, defeating this
    # function's own #128 purpose.
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
        lifespan="on",
        log_config=_build_uvicorn_log_config(LAUNCHER_LOG_DIR),
    )


def main() -> None:
    """Main entry point for the agent CLI."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="llauncher agent")
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop any running agent and exit",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["print-token"],
        help=(
            "Optional subcommand. 'print-token' resolves this node's agent "
            "token (env > stdin > ~/.llauncher/agent.env) and prints it to "
            "stdout, then exits — so an operator can copy it into the head's "
            "Add Node form without file-archaeology (issue #134)."
        ),
    )
    args = parser.parse_args()

    # Handle print-token subcommand: resolve and print the local token, then
    # exit. Never auto-generates — printing a freshly minted token from a
    # read-only command would diverge the on-disk secret; a missing token is
    # a fail-loud config error, not a trigger to mint one.
    if args.command == "print-token":
        env_token = os.environ.get("LLAUNCHER_AGENT_TOKEN")
        token = resolve_agent_token(env_value=env_token, allow_generate=False)
        if token is None:
            sys.stderr.write(
                "[llauncher-agent] ERROR: no agent token found. Start the "
                "agent once on loopback to generate one into "
                "~/.llauncher/agent.env, or set LLAUNCHER_AGENT_TOKEN.\n"
            )
            sys.exit(1)
            return
        print(token)
        sys.exit(0)
        return

    # Handle --stop flag
    if args.stop:
        config = AgentConfig.from_env()
        success = stop_agent(config.port)
        if success:
            logger.info("Agent stopped successfully")
            sys.exit(0)
        else:
            logger.info("No running agent found to stop")
            sys.exit(0)
        # If we get here, sys.exit didn't exit (e.g., because it was mocked in tests)
        return

    # Load config from environment and start agent
    config = AgentConfig.from_env()

    try:
        run_agent(config)
        # Normal exit — uvicorn's capture_signals() handler returns cleanly
        # on SIGTERM after draining in-flight requests, and the FastAPI
        # lifespan shutdown handler (per #65) has already reaped managed
        # llama-server children at this point.
        logger.info("Agent shutdown complete")
        sys.exit(0)
    except KeyboardInterrupt:
        # Some uvicorn versions re-raise KeyboardInterrupt on SIGINT after
        # the lifespan shutdown handler runs. Treat identically to a clean
        # SIGTERM exit — children have already been reaped by the lifespan
        # handler.
        logger.info("Agent shutdown complete (interrupted)")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Agent failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
