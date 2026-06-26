"""Agent authentication token resolution (security hardening §3 C1).

This module implements the token-resolution policy for the agent's HTTP
API authentication. It lives in :mod:`llauncher.core` — *below* both
``agent`` and ``remote`` in the layering — so that front-ends and the
remote-node client can read the local agent's token **without importing
``agent.*``**. That import edge (``remote.registry`` →
``agent.auth.resolve_agent_token``) was the layering violation tracked as
issue #171; hoisting the token logic here retires it.

:mod:`llauncher.agent.auth` re-exports every public name defined here, so
existing ``from llauncher.agent.auth import resolve_agent_token`` callers
keep working unchanged.

The policy in precedence order:

1. ``LLAUNCHER_AGENT_TOKEN`` env var, when set to a non-empty value
   that is *not* the literal ``"-"``. Used directly.
2. ``LLAUNCHER_AGENT_TOKEN=-`` is the explicit opt-in trigger to read
   the token from standard input (one line, stripped). Lets operators
   pipe a token from a secret manager without leaving it in the
   environment.
3. The token file ``agent.token`` under ``LAUNCHER_STATE_DIR``
   (default ``~/.llauncher``; see issue #196), when present. Read
   verbatim (stripped).
4. Otherwise, generate a fresh ``secrets.token_urlsafe(32)`` token,
   write it to that ``agent.token`` path with mode 0600 (parent
   dir 0700), print it to stderr *once*, and return it.

The auto-generation path is only safe to silently take when the
agent is bound to a loopback interface. The refuse-to-start guard
in :func:`llauncher.agent.server.run_agent` covers the non-loopback
case before auth resolution runs.
"""

from __future__ import annotations

import os
import secrets
import stat
import sys
from pathlib import Path


def default_token_path() -> Path:
    """Return the agent token path under ``LAUNCHER_STATE_DIR``.

    Derived from the single durable-state base (issue #196). With
    ``LAUNCHER_STATE_DIR`` unset this resolves to
    ``~/.llauncher/agent.token`` exactly as before. Imported lazily so
    importing this module stays filesystem-free and avoids any import
    ordering concerns.
    """
    from llauncher.core.settings import LAUNCHER_STATE_DIR

    return LAUNCHER_STATE_DIR / "agent.token"


def _read_stdin_token() -> str:
    """Read a single token line from stdin.

    Raises ``RuntimeError`` if stdin is closed or yields an empty
    value — the operator explicitly requested the stdin path with
    ``LLAUNCHER_AGENT_TOKEN=-``, so a missing token is a fatal config
    error, not a fallback trigger.
    """
    line = sys.stdin.readline()
    token = line.strip()
    if not token:
        raise RuntimeError(
            "LLAUNCHER_AGENT_TOKEN=- requested but no token was provided on stdin"
        )
    return token


def _read_token_file(path: Path) -> str | None:
    """Return the stripped token from ``path``, or None if missing/empty.

    Decoded as ``utf-8-sig`` so that a leading byte-order mark — which
    Windows PowerShell 5.1 prepends when writing with ``-Encoding utf8``
    — is consumed at decode time rather than leaking into the token as
    a ``\\ufeff`` character. ``str.strip()`` does NOT remove ``\\ufeff``
    (it's classified as zero-width non-breaking space, not whitespace),
    so a BOM left in the token used to surface downstream as an
    ``ascii``-codec ``UnicodeEncodeError`` when httpx serialized the
    token into the ``X-Api-Key`` request header. ``utf-8-sig`` is a
    strict superset of ``utf-8`` for BOM-less input, so this is a
    defensive widening with no behavior change for the BOM-free case.
    """
    try:
        data = path.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        return None
    return data or None


def _generate_and_persist_token(path: Path) -> str:
    """Generate a fresh token and persist it at ``path`` with mode 0600.

    Parent directory is created with mode 0700 if missing. The token
    is printed to stderr once so the operator can copy it into their
    client config; we deliberately avoid the regular logger because
    the operator may have a structured log pipeline that would
    otherwise capture and retain the secret indefinitely.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        parent.chmod(stat.S_IRWXU)  # 0700
    except OSError:
        # Best-effort; on some filesystems (e.g. Windows-on-NTFS via
        # WSL) chmod is a no-op. The 0600 on the file itself is the
        # load-bearing protection.
        pass

    token = secrets.token_urlsafe(32)
    # Write then chmod — open() honors umask, so we restrict
    # explicitly after creation.
    path.write_text(token + "\n", encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass

    print(
        f"[llauncher-agent] Generated new auth token at {path} (mode 0600).\n"
        f"[llauncher-agent] Token: {token}\n"
        f"[llauncher-agent] Set LLAUNCHER_AGENT_TOKEN or use this token in your client.",
        file=sys.stderr,
    )
    return token


def resolve_agent_token(
    *,
    env_value: str | None = None,
    token_path: Path | None = None,
    allow_generate: bool = True,
) -> str | None:
    """Resolve the agent auth token per the precedence chain.

    Parameters
    ----------
    env_value:
        Raw value of ``LLAUNCHER_AGENT_TOKEN`` (or ``None`` if unset).
        When ``None`` (the default kwarg), the env is read at call
        time. Pass an explicit value (including ``""``/``None``) to
        bypass the env read — used by tests.
    token_path:
        Override the on-disk token file location. Defaults to
        :func:`default_token_path`.
    allow_generate:
        When False, the auto-generate-and-write step is suppressed
        and the function returns ``None`` if no token is found via
        env/stdin/file. Used by the refuse-to-start guard so it can
        report the unauth'd-non-loopback combination before any
        token is materialized.

    Returns
    -------
    The resolved token, or ``None`` if no token could be obtained
    and ``allow_generate=False``.
    """
    if env_value is None:
        env_value = os.environ.get("LLAUNCHER_AGENT_TOKEN")

    if env_value == "-":
        return _read_stdin_token()
    if env_value:
        return env_value

    path = token_path or default_token_path()
    existing = _read_token_file(path)
    if existing:
        return existing

    if not allow_generate:
        return None

    return _generate_and_persist_token(path)
