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

**Single live source (issue #284).** The agent service and the UI/client
token resolution both parse ``LAUNCHER_STATE_DIR/agent.env`` directly at
startup — there is no separate ``agent.token`` mirror file any more. The
mirror class (a second file an installer had to keep in sync with the env
file) was the root cause of issue #281's UI-403 split-brain: an operator
edit to the env file was inert unless the installer also refreshed the
mirror. Per the repo's ``PARSE-AT-THE-DOOR`` local rule there is no
dual-read fallback onto the old file shape — nothing in this module reads
``agent.token`` any more.

The policy in precedence order:

1. ``LLAUNCHER_AGENT_TOKEN`` env var, when set to a non-empty value
   that is *not* the literal ``"-"``. Used directly — an explicit
   override always wins.
2. ``LLAUNCHER_AGENT_TOKEN=-`` is the explicit opt-in trigger to read
   the token from standard input (one line, stripped). Lets operators
   pipe a token from a secret manager without leaving it in the
   environment.
3. The ``LLAUNCHER_AGENT_TOKEN=`` line inside ``agent.env`` under
   ``LAUNCHER_STATE_DIR`` (default ``~/.llauncher``; see issue #196).
   Parsed with :func:`parse_env_file`, a simple ``KEY=VALUE`` reader
   (see that function's docstring for the exact semantics — last-wins
   on duplicate keys, matching systemd's ``EnvironmentFile=`` parser so
   both installers agree with the runtime on which line wins; see
   issue #285).
4. Otherwise, generate a fresh ``secrets.token_urlsafe(32)`` token and
   persist it *into* ``agent.env`` — rewriting in place so the file is
   left with exactly one ``LLAUNCHER_AGENT_TOKEN=`` line (any prior
   token line, e.g. an empty template placeholder, is stripped first;
   issue #293), creating the file with mode 0600, parent dir 0700 if it
   does not yet exist — print it to stderr *once*, and return it. Never
   appends a second token line: two token lines in one file is the
   split-brain footgun that reopened the UI-403 recurrence.

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
from collections.abc import Mapping
from pathlib import Path

#: Pre-rename token env var name (single-L ``LAUNCHER``), retired by the
#: #138/#139 rename (commit 9f098d9). Nothing reads it any more; its
#: presence is only ever a marker of a pre-rename deployment (#281).
LEGACY_ENV_VAR = "LAUNCHER_AGENT_TOKEN"


def legacy_token_env_misconfigured(environ: Mapping[str, str] | None = None) -> bool:
    """Return True when only the pre-rename token env var carries a value.

    Commit 9f098d9 (#138/#139) renamed ``LAUNCHER_AGENT_TOKEN`` →
    ``LLAUNCHER_AGENT_TOKEN``, but live deployments (env files written
    from the pre-rename template, service configs that re-inject them)
    can still export the legacy single-L name. Since nothing reads it,
    the agent would fall through to the token-file/auto-generate path
    and silently mint a *different* token than the one the operator
    configured — the UI-403 split-brain of issue #281.

    True iff ``LAUNCHER_AGENT_TOKEN`` is set non-empty AND
    ``LLAUNCHER_AGENT_TOKEN`` is absent or empty (empty counts as
    absent). That combination only ever means a pre-#139 deployment,
    so callers should fail loud rather than proceed.
    """
    if environ is None:
        environ = os.environ
    return bool(environ.get(LEGACY_ENV_VAR)) and not environ.get(
        "LLAUNCHER_AGENT_TOKEN"
    )


#: The key line format written by :func:`_generate_and_persist_token` and
#: read by :func:`parse_env_file`.
_TOKEN_KEY = "LLAUNCHER_AGENT_TOKEN"


def default_env_path() -> Path:
    """Return the live ``agent.env`` path under ``LAUNCHER_STATE_DIR``.

    Derived from the single durable-state base (issue #196). With
    ``LAUNCHER_STATE_DIR`` unset this resolves to
    ``~/.llauncher/agent.env``. Imported lazily so importing this module
    stays filesystem-free and avoids any import ordering concerns.

    This is the single live source of the agent's configuration
    (issue #284): both the agent service and the UI/client token
    resolution parse this exact file directly at startup. There is no
    installer-time snapshot and no separate token-mirror file.
    """
    from llauncher.core.settings import LAUNCHER_STATE_DIR

    return LAUNCHER_STATE_DIR / "agent.env"


def _strip_bom(value: str) -> str:
    """Strip a leading UTF-8 BOM (``\\ufeff``) from ``value``, if present.

    ``str.strip()`` does **not** remove ``\\ufeff`` — it is a zero-width
    non-breaking space, not whitespace by Python's ``str.isspace()``
    classification — so a BOM that survives into an in-memory string (stdin,
    an env var) needs an explicit strip rather than a plain ``.strip()``
    call (issue #127). File-read call sites already decode with
    ``utf-8-sig`` (see :func:`parse_env_file`), which consumes a leading BOM
    at decode time; this helper covers the two sources that do not go
    through that decode step.
    """
    return value.lstrip("﻿")


def _read_stdin_token() -> str:
    """Read a single token line from stdin.

    Raises ``RuntimeError`` if stdin is closed or yields an empty
    value — the operator explicitly requested the stdin path with
    ``LLAUNCHER_AGENT_TOKEN=-``, so a missing token is a fatal config
    error, not a fallback trigger. The line is BOM-stripped before the
    empty-check (issue #127) so a BOM-only stdin line still raises
    ``RuntimeError`` rather than resolving to a token consisting only of
    an invisible character.
    """
    line = sys.stdin.readline()
    token = _strip_bom(line.strip())
    if not token:
        raise RuntimeError(
            "LLAUNCHER_AGENT_TOKEN=- requested but no token was provided on stdin"
        )
    return token


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` env file into a dict, or ``{}`` if missing.

    A minimal, hand-rolled reader matching systemd's ``EnvironmentFile=``
    semantics (see ``man systemd.exec``), so the installers and the
    runtime agree on which value wins. ``python-dotenv`` (already a hard
    dependency; see ``llauncher/__init__.py``'s ``load_dotenv()``) was
    considered and rejected here: ``dotenv_values()`` prints a parser
    warning to stderr and yields a ``None``-valued key for a malformed
    line (e.g. one with no ``=``) rather than silently skipping it — a
    behavior mismatch with the "tolerant, silent skip" contract this
    function is specified to have (a hand-edited ``agent.env`` with a
    stray line must not spam stderr on every agent start). The overlap
    that *does* match (last-wins on duplicate keys, ``utf-8-sig``
    decoding) is small enough that duplicating it here, fully pinned by
    the tests below, was judged cheaper than papering over
    ``dotenv_values``'s edge-case differences at every call site.

    - Blank lines are skipped.
    - Comment lines (first non-whitespace character is ``#``) are
      skipped.
    - Each remaining line is split on the first ``=`` into a key and a
      value; the key is stripped of surrounding whitespace, the value
      is stripped of a trailing newline only (leading/trailing spaces
      inside the value are preserved verbatim, matching systemd — quote
      your value in the file if you need to pad it).
    - A line with no ``=`` is skipped (not a fatal error — matches
      systemd's tolerant parsing rather than failing the whole file
      over one malformed line).
    - **Last-wins** on a duplicate key: a later line silently
      overwrites an earlier line's value for the same key. This is
      systemd's ``EnvironmentFile=`` behavior exactly; both installers
      must agree with this reader on that point (issue #285).

    Decoded as ``utf-8-sig`` so that a leading byte-order mark — which
    Windows PowerShell 5.1 prepends when writing with ``-Encoding utf8``
    — is consumed at decode time rather than leaking a ``\\ufeff``
    character into the first key's name or value. ``utf-8-sig`` is a
    strict superset of ``utf-8`` for BOM-less input, so this is a
    defensive widening with no behavior change for the BOM-free case.

    Returns ``{}`` (not an error) when ``path`` does not exist — a
    missing env file is a normal pre-first-run state, not a fault.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}

    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = _strip_bom(key.strip())
        if not key:
            continue
        result[key] = value.strip()
    return result


def _read_env_file_token(path: Path) -> str | None:
    """Return the ``LLAUNCHER_AGENT_TOKEN`` value from ``path``, or None.

    Thin wrapper over :func:`parse_env_file` scoped to the one key this
    module cares about; an empty value (``LLAUNCHER_AGENT_TOKEN=`` with
    nothing after the ``=``) counts as absent, matching the env-var
    precedence step above it.
    """
    return parse_env_file(path).get(_TOKEN_KEY) or None


def count_env_file_token_lines(path: Path) -> int:
    """Return how many ``LLAUNCHER_AGENT_TOKEN=`` lines ``path`` contains.

    Counts physical lines whose key (after optional leading whitespace,
    up to the first ``=``) is exactly ``LLAUNCHER_AGENT_TOKEN`` — the same
    key-extraction :func:`parse_env_file` uses, so a commented
    ``# LLAUNCHER_AGENT_TOKEN=`` line or a same-substring inside another
    value never counts. Returns ``0`` for a missing file.

    Two or more is the duplicate-token split-brain (#293): the resolvers
    are all last-wins (#284/d5f83b9), so a duplicate does not by itself
    change which value wins, but it is the footgun a later hand-edit trips
    into a server/client mismatch — the agent's startup guard fails loud on
    it rather than running with a latent split.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return 0
    count = 0
    for raw_line in text.splitlines():
        stripped = raw_line.lstrip()
        key = _strip_bom(stripped.partition("=")[0].rstrip())
        if key == _TOKEN_KEY:
            count += 1
    return count


def _strip_token_lines(text: str) -> str:
    """Return ``text`` with every ``LLAUNCHER_AGENT_TOKEN=`` line removed.

    Anchors on the canonical token key at line start (after optional
    leading whitespace), matching :func:`parse_env_file`'s key extraction,
    so a commented line (``# LLAUNCHER_AGENT_TOKEN=...``) or a same-value
    substring inside another key's value is never mistaken for a token
    line. Preserves every other line verbatim, including blank and comment
    lines, and preserves whether the input ended with a newline.
    """
    kept: list[str] = []
    for raw_line in text.splitlines(keepends=True):
        stripped = raw_line.lstrip()
        key = _strip_bom(stripped.partition("=")[0].rstrip())
        if key == _TOKEN_KEY:
            continue
        kept.append(raw_line)
    return "".join(kept)


def _generate_and_persist_token(path: Path) -> str:
    """Generate a fresh token and rewrite it into ``agent.env`` at ``path``.

    **Rewrite in place, never append (issue #293).** If ``path`` does not
    exist yet, it is created (mode 0600, parent dir 0700) containing just
    the token line. If it already exists (reached only when it has no
    *usable* ``LLAUNCHER_AGENT_TOKEN=`` line — an empty or absent value),
    every existing ``LLAUNCHER_AGENT_TOKEN=`` line is stripped first and
    exactly one canonical line is written, so the file always ends with a
    single token line. The earlier append-only behavior could leave a
    second ``LLAUNCHER_AGENT_TOKEN=`` line behind (e.g. an empty
    ``LLAUNCHER_AGENT_TOKEN=`` template line the operator never filled),
    and although every resolver is last-wins as of #284/d5f83b9, two token
    lines is the split-brain footgun that reopened the UI-403 recurrence
    (server and client resolving different values the moment a later edit
    reorders them). PARSE-AT-THE-DOOR: migrate to a single canonical line,
    once — never leave two shapes of the token in one file.

    The file's mode is left as-is when it already exists, since it
    presumably already carries whatever permissions the installer or a
    previous run set — e.g. systemd ``--system`` mode's 0640/group-
    ``inference`` ``agent.env``, which the UI's group-read access depends
    on. Tightening it to 0600 here would silently break that cross-process
    read path; the new-file case has no such prior permission to preserve,
    so it gets the same 0600/0700 hardening as before.

    A missing trailing newline on the last preserved line is repaired so
    the appended token key lands on its own physical line and stays
    parseable by :func:`parse_env_file`.

    The token is printed to stderr once so the operator can copy it
    into their client config; we deliberately avoid the regular logger
    because the operator may have a structured log pipeline that would
    otherwise capture and retain the secret indefinitely.
    """
    token = secrets.token_urlsafe(32)
    is_new_file = not path.exists()

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if is_new_file:
        try:
            parent.chmod(stat.S_IRWXU)  # 0700
        except OSError:
            # Best-effort; on some filesystems (e.g. Windows-on-NTFS via
            # WSL) chmod is a no-op. The 0600 on the file itself is the
            # load-bearing protection.
            pass

    line = f"{_TOKEN_KEY}={token}\n"
    if is_new_file:
        path.write_text(line, encoding="utf-8")
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass
    else:
        # Rewrite in place: drop any existing token line(s) so the file is
        # left with exactly one canonical token line, then re-write the
        # whole file (preserving its inode's permissions, since we open the
        # existing path for write rather than replacing it).
        preserved = _strip_token_lines(path.read_text(encoding="utf-8-sig"))
        if preserved and not preserved.endswith("\n"):
            preserved += "\n"
        path.write_text(preserved + line, encoding="utf-8")

    print(
        f"[llauncher-agent] Generated new auth token and wrote it to {path} "
        f"(LLAUNCHER_AGENT_TOKEN=...).\n"
        f"[llauncher-agent] Token: {token}\n"
        f"[llauncher-agent] Set LLAUNCHER_AGENT_TOKEN or use this token in your client.",
        file=sys.stderr,
    )
    return token


def resolve_agent_token(
    *,
    env_value: str | None = None,
    env_path: Path | None = None,
    allow_generate: bool = True,
) -> str | None:
    """Resolve the agent auth token per the precedence chain.

    Parameters
    ----------
    env_value:
        Raw value of ``LLAUNCHER_AGENT_TOKEN`` (or ``None`` if unset).
        When ``None`` (the default kwarg), the env is read at call
        time. Pass an explicit value (including ``""``/``None``) to
        bypass the env read — used by tests. This is the explicit
        override step (precedence 1) and always wins when non-empty.
    env_path:
        Override the on-disk ``agent.env`` location. Defaults to
        :func:`default_env_path`. This is the single live source
        (issue #284) both the agent and the UI/client parse directly;
        there is no separate token-mirror file.
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

    if env_value is not None:
        # BOM-strip before the "-" comparison (issue #127) so a
        # BOM-prefixed "-" (e.g. from a Windows-authored env block) still
        # triggers the stdin path rather than being treated as a literal
        # (and useless) token value.
        env_value = _strip_bom(env_value)

    if env_value == "-":
        return _read_stdin_token()
    if env_value:
        return env_value

    path = env_path or default_env_path()
    existing = _read_env_file_token(path)
    if existing:
        return existing

    if not allow_generate:
        return None

    return _generate_and_persist_token(path)
