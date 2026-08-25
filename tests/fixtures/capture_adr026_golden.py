"""Capture the pre-#477 ``build_command`` argv for the ADR-026 golden corpus.

NOT a test — pytest does not collect this file. It is the harness that
produced ``tests/fixtures/adr026_pre477_argv_golden.json``, kept beside the
golden so the golden is reproducible rather than merely asserted.

The point of a *captured* golden: the expected argv in that JSON came out of
the real pre-#477 ``build_command``, not out of a reimplementation of it
written from the same assumptions as the code under test. Run it against a
checkout of the branch point of #477::

    git worktree add --detach /tmp/pre477 3fdef15
    PYTHONPATH=/tmp/pre477 python tests/fixtures/capture_adr026_golden.py         tests/fixtures/adr026_pre477_argv_golden.json

Corpus notes: ``cache_type_k``/``v`` use ``q8_0``, not #477's motivating
``q4_0`` — the pre-#477 ``Literal`` could not hold ``q4_0`` at all (that
expressiveness ceiling *is* the bug), so no pre-migration argv exists to
capture for it. ``q4_0`` is covered by the non-golden migration tests.
"""
import json
import sys
from pathlib import Path

from llauncher.core.process import build_command
from llauncher.models.config import ModelConfig


def base(**over):
    e = {
        "name": "m",
        "model_path": "/fake/m.gguf",
        "n_gpu_layers": 255,
        "ctx_size": 4096,
        "threads": None,
        "threads_batch": 8,
        "ubatch_size": 512,
        "batch_size": None,
        "flash_attn": "on",
        "no_mmap": False,
        "cache_type_k": None,
        "cache_type_v": None,
        "n_cpu_moe": None,
        "parallel": 1,
        "temperature": None,
        "top_k": None,
        "top_p": None,
        "min_p": None,
        "repeat_penalty": None,
        "reverse_prompt": None,
        "mlock": False,
        "metrics": True,
        "slots": False,
        "extra_args": "",
    }
    e.update(over)
    return e


CORPUS = {
    "defaults_only": base(),
    "threads_set": base(threads=4),
    "threads_batch_nondefault": base(threads_batch=16),
    "ubatch_size_nondefault": base(ubatch_size=1024),
    "batch_size_set": base(batch_size=2048),
    "flash_attn_auto": base(flash_attn="auto"),
    "flash_attn_off": base(flash_attn="off"),
    "no_mmap_true": base(no_mmap=True),
    "cache_type_k_set": base(cache_type_k="q8_0"),
    "cache_type_v_set": base(cache_type_v="q8_0"),
    "n_cpu_moe_set": base(n_cpu_moe=4),
    "temperature_set": base(temperature=0.7),
    "top_k_set": base(top_k=40),
    "top_p_set": base(top_p=0.9),
    "min_p_set": base(min_p=0.05),
    "repeat_penalty_set": base(repeat_penalty=1.1),
    "reverse_prompt_set": base(reverse_prompt="STOP"),
    "mlock_true": base(mlock=True),
    "opaque_extra_args": base(extra_args="--embeddings --log-disable"),
    "live_embedding_ubatch_collision": base(
        ubatch_size=512,
        extra_args="--embeddings --log-disable --ubatch-size 2048 --batch-size 2048",
    ),
    "short_alias_ctk_ctv_collision": base(
        cache_type_k="q8_0", cache_type_v="q8_0", extra_args="-ctk f16 -ctv f16"
    ),
    "short_alias_fa_collision": base(flash_attn="on", extra_args="-fa off"),
    "short_alias_tb_ub_collision": base(
        threads_batch=8, ubatch_size=512, extra_args="-tb 16 -ub 2048"
    ),
    "short_alias_t_b_collision": base(
        threads=4, batch_size=2048, extra_args="-t 8 -b 4096"
    ),
    "long_form_collision": base(
        cache_type_v="q8_0", extra_args="--cache-type-v f16"
    ),
    "mmproj_and_parallel": base(
        mmproj_path="/fake/mm.gguf", parallel=4, slots=True, metrics=False
    ),
}

out = []
for cid, entry in CORPUS.items():
    cfg = ModelConfig.from_dict_unvalidated(dict(entry))
    argv = build_command(
        cfg, port=8080, host="127.0.0.1", server_bin=Path("llama-server")
    )
    out.append({"id": cid, "entry": entry, "argv": argv})

Path(sys.argv[1]).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
print(f"captured {len(out)} entries")
