# ADR 001: Dynamic Context Window Budget from llauncher

## Status: Proposed

## Date: 2026-04-26

## Context

Pi's built-in footer displays context usage as `P.P%/TTTk (auto)`, where `TTk` is the context window total. Pi defaults unknown providers to a hardcoded **128,000** tokens (`contextWindow: 128_000` in `model-registry.js`). Self-hosted models loaded through llauncher have real context windows that vary per model and can differ significantly — e.g., Qwen3.6-35B-A3B-GGUF with `ctx_size=400,000`, `parallel=3` yields **133,333 effective tokens** (1/3 the total because three sessions share one KV cache). The displayed percentage is therefore miscalculated: it divides by 128k instead of ~133k.

There is no Pi extension hook to "override just this value." Extensions that call `ctx.ui.setFooter()` **completely replace** Pi's footer component. The extension must replicate all formatting, colorization, padding, and layout — only the context window math differs from default behavior.

Pi's token counting (`getContextUsage()`) is post-compaction-aware and uses the last LLM response's actual `totalTokens` plus a char/4 heuristic for trailing messages. It returns `{ tokens: null }` briefly after compaction until the next response confirms counts. This is acceptable — for budgets >100k tokens, minor imprecision in the numerator is negligible; what matters is using the correct denominator.

## Decision

Replace Pi's hardcoded `128k` context window in footer rendering with a dynamically resolved value from llauncher:

```
effectiveWindow = ctx_size / parallel   // real per-session KV cache pool
percentage      = (tokens / effectiveWindow) × 100   // recalculated by the extension
displayTotal    = formatTokens(effectiveWindow)       // replaces 128k in output
```

The footer output format is identical to Pi's built-in: `↑input ↓output RcacheRead $cost P.P%/TTTk (auto)` right-aligned to model name on the right side. Same dim styling, same color thresholds at 70% and 90%.

## Consequences

### Positive
- **Accurate budget awareness**: percentage reflects real KV cache utilization for llauncher-loaded models
- **Zero new config files**: llauncher discovery via `$LLAUNCHER_HOST` env var — already in `docker-compose.yml`. Port 8765 is llauncher's well-known agent address.
- **Automatic activation**: extension activates on every `session_start`; no slash commands, no manual toggles, no user-facing control surface to manage for this version
- **Graceful degradation**: if `$LLAUNCHER_HOST` is unset or unreachable, the extension falls back to Pi's own `model.contextWindow` then to 128k. The footer still renders; only the denominator precision degrades

### Negative / Trade-offs
- **Replication burden**: because extensions replace entirely, any future changes to Pi's footer layout (new stat columns, padding changes, thinking level display) must be manually replicated in this extension
- **One-time launcher fetch**: `ctx_size / parallel` is read once at session start. If the loaded model changes mid-session without a new session, stale values persist. Acceptable for typical usage patterns where swapping models triggers a session change
- **Post-compaction null**: after compaction, `getContextUsage()` returns `{ tokens: null }`. The extension displays `?/TTTk (auto)` rather than guessing from accumulated totals — correct behavior but visually signals "unknown" briefly during normal operation

### Scope boundaries
- **Llauncher only.** This extension is scoped to llama-server loaded via llauncher. It does not support LM Studio, Ollama, vLLM, or cloud APIs that report their own context limits natively
- **Per-session budget only.** The extension calculates per-session KV cache utilization from the running server's `parallel` slot count. Multi-session sharing across slots is correctly represented by the `/ parallel` division

### Open Questions
1. Should the extension periodically re-fetch llauncher status in case the loaded model changes mid-session? (Out of scope for v1)
2. Should there be a toggle/slash command to disable the extension and restore Pi's built-in footer? (Deferred — decision pending user feedback after deployment)
