"""EMIT-CANONICAL live proof — the ONE-MINT keystone, asserted end-to-end.

llauncher/CLAUDE.md records the ecosystem-physics obligation this file
checks: ``ModelConfig.name`` is the single authority for local-model
identity, and every server llauncher starts must report that exact name
on ``GET /v1/models`` — not a GGUF-filename-derived string, not a path.
The wire-level mechanism is ``core/process.py::build_command`` appending
``--alias <ModelConfig.name>``, with ``--alias`` kept out of
``DENIED_EXTRA_ARG_FLAGS`` reach so no config can override the minted
identity (issues #120/#87/#10).

Neither ``test_self_swap.py::test_self_swap_live_completion_against_new_model``
nor ``test_server_metrics_live.py::test_server_metrics_live_reports_phase_and_rate``
asserts this — both use ``model_name`` equal to the registered config name
incidentally, and neither reads ``/v1/models`` at all. This file starts a
real server through llauncher's own dispatch table (no shortcuts) and
inspects the actual wire response.

Response shape verified live against build 10481 (commit 25ae3a9b3,
2026-08-18) — llama-server's ``/v1/models`` mixes an Ollama-style
``models[]`` list (``name``/``model`` fields) with an OpenAI-style
``data[]`` list (``id`` field, plus an ``aliases[]`` array). Both surfaces
carry the ``--alias`` value verbatim; this test checks both so a future
llama.cpp response-shape change that silently drops one surface is caught.
"""

from __future__ import annotations

import time

import httpx
import pytest

from llauncher.core.config import ConfigStore
from llauncher.models.config import ModelConfig


pytestmark = pytest.mark.integration


def _free_port() -> int:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.live
@pytest.mark.integration_real
async def test_emit_canonical_v1_models_reports_minted_name(
    real_binary_env, mcp_dispatch
):
    """``GET /v1/models`` reports ``ModelConfig.name`` byte-for-byte.

    The registered name is deliberately unrelated to the GGUF filename
    (unlike ``alpha``/``beta`` in the swap tests, which happen to look
    like they *could* be filename-derived) so a false-pass via accidental
    string overlap is not possible: proving the wire name equals the
    *registered* name, not merely equals *some* plausible string, is the
    entire point of ONE-MINT/EMIT-CANONICAL.
    """
    gguf = real_binary_env["gguf"]
    canonical_name = "emit-canonical-keystone-check-not-a-filename"
    assert canonical_name != gguf.stem, "test name must not equal the GGUF stem"

    cfg = ModelConfig.from_dict_unvalidated(
        {
            "name": canonical_name,
            "model_path": str(gguf),
            "n_gpu_layers": 0,
            "ctx_size": 512,
            "threads_batch": 1,
            "ubatch_size": 1,
            "flash_attn": "off",
        }
    )
    ConfigStore.add_model(cfg, caller="emit-canonical-live-test")

    port = _free_port()

    start = await mcp_dispatch(
        "start_server", {"model_name": canonical_name, "port": port}
    )
    assert start["success"], start

    try:
        base = f"http://127.0.0.1:{port}"

        # Wait for /health, then read /v1/models — the actual assertion
        # surface. Do not assume readiness implies /v1/models is already
        # populated; poll it too.
        deadline = time.monotonic() + 30.0
        payload = None
        while time.monotonic() < deadline:
            try:
                r = httpx.get(f"{base}/v1/models", timeout=1.0)
                if r.status_code == 200:
                    body = r.json()
                    if body.get("data") or body.get("models"):
                        payload = body
                        break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)

        assert payload is not None, "server never served a populated /v1/models"

        # OpenAI-compatible surface: data[].id (and aliases[]) must be the
        # exact canonical name — byte-for-byte, no transformation.
        data_entries = payload.get("data", [])
        assert data_entries, f"/v1/models 'data' list empty: {payload!r}"
        assert data_entries[0]["id"] == canonical_name, (
            f"EMIT-CANONICAL violation: /v1/models data[0].id="
            f"{data_entries[0]['id']!r}, expected {canonical_name!r} "
            f"(ModelConfig.name, byte-for-byte). Full payload: {payload!r}"
        )
        assert canonical_name in data_entries[0].get("aliases", []), (
            f"EMIT-CANONICAL violation: canonical name not in aliases: "
            f"{data_entries[0]!r}"
        )

        # Ollama-compatible surface: models[].name must match too.
        models_entries = payload.get("models", [])
        assert models_entries, f"/v1/models 'models' list empty: {payload!r}"
        assert models_entries[0]["name"] == canonical_name, (
            f"EMIT-CANONICAL violation: /v1/models models[0].name="
            f"{models_entries[0]['name']!r}, expected {canonical_name!r}. "
            f"Full payload: {payload!r}"
        )

        # Negative control, inline: the canonical name must not equal any
        # path/filename-derived string a regressed build_command might
        # emit instead (e.g. the GGUF stem, or the model_path itself).
        assert data_entries[0]["id"] != gguf.stem
        assert data_entries[0]["id"] != str(gguf)
    finally:
        await mcp_dispatch("stop_server", {"port": port})
