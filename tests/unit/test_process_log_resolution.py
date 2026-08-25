"""Regression tests for issues #145 and #146/#63 — per-server log resolution.

#145: `wait_for_server_ready` confirms a server is up by scanning its log
for a "listening" line. The bug: it resolved the log by **port alone**
(`glob("*-{port}.log")`, first match), but across a swap two such files
coexist — the stopped occupant's and the new one's — so it could read the
stopped model's log (whose tail is shutdown noise, no "listening") and time
the swap out even though the new server was healthy.

The fix threads the model name through `wait_for_server_ready` so it reads
that model's *exact* log via `log_path_for`, and hardens `stream_logs` to
derive the on-disk stem via the same mint and prefer the most-recently-
written match.

#146/#63: the original name→filename sanitizer was lossy (`model.a` and
`model_a` collapsed onto one file, silently interleaving two models' logs
under ADR-LLNCH-013 append mode). `log_stem_for` is now the single injective
mint: sanitized stem plus a short stable hash of the exact canonical name.
"""

from __future__ import annotations

import re
import socket
from contextlib import closing

import pytest

from llauncher.core import process as proc


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """Point process.LOG_DIR at an isolated, resolved tmp dir."""
    d = (tmp_path / "logs").resolve()
    d.mkdir()
    monkeypatch.setattr(proc, "LOG_DIR", d)
    return d


def _write(path, *lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─────────────────── log_stem_for / log_path_for ──────────────────


def test_log_path_for_keeps_sanitized_stem_readable(log_dir):
    """The on-disk name stays greppable: sanitized name (dots → underscores)
    plus an 8-hex-char disambiguator, then the port."""
    p = proc.log_path_for("LFM2-350M-Pro.f16", 8082)
    assert re.fullmatch(r"LFM2-350M-Pro_f16-[0-9a-f]{8}-8082\.log", p.name)
    assert p.parent == log_dir


def test_log_path_for_separates_names_that_sanitize_alike(log_dir):
    """#146/#63 regression: names differing only in non-[\\w-] chars used to
    collapse onto ONE file (lossy sanitizer), silently interleaving two
    models' logs. The hash suffix must map them apart. Inverts the old
    `test_log_path_for_is_lossy_collision_is_characterized` guard."""
    assert proc.log_path_for("a.b", 9000) != proc.log_path_for("a_b", 9000)
    # The exact pairs from the issue reports:
    assert proc.log_path_for("LFM2-350M-Pro.f16", 8080) != proc.log_path_for(
        "LFM2-350M-Pro_f16", 8080
    )
    assert proc.log_path_for("Gemma-2-9b-ä", 8080) != proc.log_path_for(
        "Gemma-2-9b-!", 8080
    )


def test_log_stem_for_is_stable_and_port_independent(log_dir):
    """The mapping is deterministic (same name → same stem, every call) and
    carries no port: the port belongs to log_path_for's envelope only."""
    stem1 = proc.log_stem_for("model.v1")
    stem2 = proc.log_stem_for("model.v1")
    assert stem1 == stem2
    # Same name across ports shares the stem; only the port suffix differs.
    assert proc.log_path_for("model.v1", 8081).name == f"{stem1}-8081.log"
    assert proc.log_path_for("model.v1", 8082).name == f"{stem1}-8082.log"


def test_log_stem_for_is_glob_safe(log_dir):
    """The stem contains only [\\w-] chars — no glob metacharacters survive,
    so stream_logs can embed it literally in a glob pattern."""
    for name in ("Llama[5B]", "a*b?", "weird name!.gguf"):
        assert re.fullmatch(r"[\w\-]+", proc.log_stem_for(name))


# ──────────────────── read_logs_for_port (#201) ───────────────────


def test_read_logs_for_port_returns_none_when_no_file(log_dir):
    """No ``*-{port}.log`` on disk → None, so the agent can map it to 404."""
    assert proc.read_logs_for_port(8089) is None


def test_read_logs_for_port_tails_without_a_live_process(log_dir):
    """The death cause of an immediately-exited server is retrievable from
    disk with no live pid — the heart of #201 Part 2b."""
    f = proc.log_path_for("BrokenModel", 8081)
    _write(f, "=== started ===", "error while loading shared libraries: libfoo.so")
    lines = proc.read_logs_for_port(8081, lines=20)
    assert lines is not None
    assert any("shared libraries" in ln for ln in lines)


def test_read_logs_for_port_prefers_freshest_file(log_dir):
    """Across a swap two ``*-{port}.log`` may coexist; serve the newest."""
    import os
    import time

    old = log_dir / "OldModel-8082.log"
    new = log_dir / "NewModel-8082.log"
    _write(old, "old occupant line")
    _write(new, "new occupant line")
    now = time.time()
    os.utime(old, (now - 100, now - 100))
    os.utime(new, (now, now))

    lines = proc.read_logs_for_port(8082, lines=20)
    assert any("new occupant" in ln for ln in lines)
    assert all("old occupant" not in ln for ln in lines)


def test_read_logs_for_port_empty_file_is_empty_list_not_none(log_dir):
    """An existing but empty log → [] (200 with no lines), distinct from a
    missing file (None → 404)."""
    (log_dir / "EmptyModel-8083.log").write_text("", encoding="utf-8")
    assert proc.read_logs_for_port(8083) == []


# ─────────────────────── stream_logs ─────────────────────────────


def test_newest_log_prefers_freshest_match(log_dir):
    """The port/name glob disambiguator returns the freshest file, not an
    arbitrary first glob hit — the heart of the #145 fix. Two models left a
    ``*-{port}.log`` behind across a swap; the new occupant's is newer."""
    import os
    import time

    old = log_dir / "OldModel-8082.log"
    new = log_dir / "NewModel-8082.log"
    _write(old, "cleaning up before exit")
    _write(new, "server is listening on http://0.0.0.0:8082")
    now = time.time()
    os.utime(old, (now - 100, now - 100))
    os.utime(new, (now, now))

    chosen = proc._newest_log(log_dir.glob("*-8082.log"))
    assert chosen == new


def test_newest_log_empty_is_none(log_dir):
    """No matches → None (callers fall through to an empty log list)."""
    assert proc._newest_log(log_dir.glob("*-9999.log")) is None


def test_stream_logs_by_model_name_round_trips_the_mint(log_dir):
    """A file written under log_path_for resolves back via the raw config
    name — write side and read side share the one mint (log_stem_for)."""
    f = proc.log_path_for("LFM2-350M-Pro.f16", 8083)
    _write(f, "server is listening on http://0.0.0.0:8083")
    # Caller passes the raw, canonical config name.
    lines = proc.stream_logs(model_name="LFM2-350M-Pro.f16", lines=20)
    assert any("listening" in ln for ln in lines)


def test_stream_logs_by_model_name_handles_glob_metachars(log_dir):
    """A name containing glob metachars (``[``) must not be treated as a
    pattern; the stem is metachar-free so the lookup is well-defined."""
    f = proc.log_path_for("Llama[5B]", 8080)
    _write(f, "rest api listening")
    lines = proc.stream_logs(model_name="Llama[5B]", lines=20)
    assert any("listening" in ln for ln in lines)


def test_stream_logs_colliding_names_read_back_only_their_own_lines(log_dir):
    """#146/#63 round-trip: two models whose names sanitize identically each
    write to their own file and each read back ONLY their own lines."""
    _write(proc.log_path_for("model.v1", 8081), "DOTTED model line")
    _write(proc.log_path_for("model_v1", 8081), "UNDERSCORE model line")

    dotted = proc.stream_logs(model_name="model.v1", lines=20)
    underscored = proc.stream_logs(model_name="model_v1", lines=20)

    assert dotted == ["DOTTED model line"]
    assert underscored == ["UNDERSCORE model line"]


# ─────────────────── wait_for_server_ready (#145 core) ────────────


@pytest.fixture
def listening_port():
    """Bind+listen a real socket so the port-open precheck passes; yield port."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        yield s.getsockname()[1]


def test_ready_reads_model_specific_log_not_stale_sibling(log_dir, listening_port):
    """#145: with a stale ``*-{port}.log`` (no indicator) AND the new model's
    log (has 'listening'), readiness keyed by model_name must succeed."""
    port = listening_port
    # Stale stopped-occupant log: shutdown noise, NO ready indicator.
    _write(
        proc.log_path_for("OldModel", port),
        "=== started ===",
        "cleaning up before exit",
    )
    # New occupant's log: contains the ready indicator.
    _write(
        proc.log_path_for("NewModel", port),
        "=== started ===",
        "loading model",
        f"server is listening on http://0.0.0.0:{port}",
    )

    ready, logs = proc.wait_for_server_ready(
        port, timeout=3, check_interval=0.02, model_name="NewModel"
    )
    assert ready is True
    assert any("listening" in ln.lower() for ln in logs)


def test_ready_keys_on_named_model_only(log_dir, listening_port):
    """Negative side of the asymmetry: pointed at a model whose own log has
    no indicator, readiness times out — it must NOT fall through to a
    sibling's 'listening' line on the same port."""
    port = listening_port
    _write(
        proc.log_path_for("OldModel", port),
        "cleaning up before exit",  # no indicator
    )
    _write(
        proc.log_path_for("NewModel", port),
        f"server is listening on http://0.0.0.0:{port}",
    )

    ready, _ = proc.wait_for_server_ready(
        port, timeout=0.3, check_interval=0.02, model_name="OldModel"
    )
    assert ready is False


def test_ready_not_shadowed_by_sanitize_alike_sibling(log_dir, listening_port):
    """#146 readiness round-trip: a sibling whose name sanitizes identically
    (``model_v1`` vs ``model.v1``) must not satisfy — or pollute — the
    readiness read for the model actually being waited on."""
    port = listening_port
    # The sanitize-alike sibling HAS the indicator…
    _write(
        proc.log_path_for("model_v1", port),
        f"server is listening on http://0.0.0.0:{port}",
    )
    # …but the model we wait on does not. Pre-fix these were ONE file and
    # the sibling's "listening" line leaked into model.v1's readiness read.
    _write(
        proc.log_path_for("model.v1", port),
        "loading model",
    )

    ready, _ = proc.wait_for_server_ready(
        port, timeout=0.3, check_interval=0.02, model_name="model.v1"
    )
    assert ready is False
