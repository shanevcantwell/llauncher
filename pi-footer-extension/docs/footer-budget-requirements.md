# Footer-Budget Extension — Requirements

## Purpose

A custom TUI footer extension for self-hosted / llaunched inference models that replaces Pi's built-in footer. It displays running token stats and **remaining context budget** using the real context window size from llauncher (not hardcoded defaults), so users can see actual KV cache utilization in their session.

## Where it applies

- Self-hosted providers only: `inference-host`, `ollama`, `lm-studio`, `vllm`
- Not applicable for cloud APIs (Anthropic, OpenAI, etc.) that report their own context limits
- Replaces Pi's built-in footer entirely (`ctx.ui.setFooter`)

## Data sources

### Context window size — from llauncher `/status`
- Read model metadata from the llaunched node's `/status` endpoint via `~/.llauncher/nodes.json`.
- **Match by running session, not by taking slot 0.** The `/status` response contains a list of running servers with their config/model info. The extension must identify which server entry corresponds to *this* Pi session's model (matched against `ctx.model.id` / provider/model string), then read its `model_config.ctx_size` and `parallel`.
- If no matching server is found, fall back to what Pi knows about the model (`ctx.model.contextWindow`), but do not silently use a hardcoded default.
- Effective per-session window = `Math.floor(ctx_size / parallel)`.

### Current context usage — from Pi's own calculation
- Call `agentSession.getContextUsage()` which returns `{ tokens, percent }` for *this* session.
- This is the authoritative source: it uses the last post-compaction model response's actual `totalTokens`, plus chars/4 heuristic for messages after that call. It does **not** sum accumulated API calls across turns (which double-counts overlapping prompt history).
- Guard against `undefined` return value from `getContextUsage()` to prevent runtime crashes.

### Running totals — cosmetic stats only
- Sum `input`, `output`, `cacheRead` across all assistant messages in the current session branch.
- These are purely informational (↑, ↓, R values) and are **not** used for budget math. The extension does not attempt to compute "remaining tokens" from these accumulated sums — that has been proven unreliable due to overlapping prompt history across turns.

## What the footer displays

### Left side: token stats
```
↑inputCount  ↓outputCount  RcacheRead  remainingContext
121k         39k           2.2M        58k
```

- `↑`, `↓`, `R` are accumulated totals (cosmetic, same as Pi's built-in footer).
- **Remaining context** is a single number: `contextWindow - currentTokens`. No bar visualization. Displayed only when we have both a valid context window and a current-context measurement from Pi.

### Right side: model identity
```
ModelName xParallelCount    (e.g., "Qwen3.6-35B-A3B-GGUF x2")
```

- Shows the running server's `config_name` when available.
- If parallel > 1, appends `xN` to indicate how many sessions share this slot pool.
- Falls back to `[provider] [modelId]` from `ctx.model` if no llauncher data is available or matching.

### Conditional rendering

| State | Remaining field shown? | Right side shown? | Example output |
|---|---|---|---|
| Valid context window + model ran at least once | Yes: formatted remaining tokens | Yes (model name) | `↑12k ↓3k R400k 89k Qwen-35B x2` |
| Valid context window, fresh session (no model response yet) | No (nothing to show — nothing consumed yet) | Yes | `↑0 ↓0 Qwen-35B x2` |
| Valid context window, post-compaction before next response | No (don't guess from accumulated totals) | Yes | `↑142k ↓48k R9.1M Qwen-35B x2` |
| Context window unknown + no matching server | Nothing on left side | Model name / provider | `↑12k ↓3k inference-host gpt-4o-mini` |

**Do not display a bar or any proportional visualization.** The remaining context is presented as a plain number. Bars were previously attempted and discarded due to the unreliability of the underlying source data.

## What it does NOT do

- No token budget bar — bars imply precision that accumulated token counts don't provide
- Does not sum `inputTokens` for budget math — historical API call totals are not a measure of current KV cache occupancy
- Does not read KV cache occupancy directly from llama.cpp (not available)
- Does not claim accuracy beyond what Pi's `getContextUsage()` provides: an estimate bounded by actual API response counts, not ground-truth memory usage

## Controls

Three slash commands provided for user control:
- `/footer-budget-toggle` — toggle on/off
- `/footer-budget-on` — enable explicitly  
- `/footer-budget-off` — restore Pi's built-in footer

## Error handling

- If llauncher config cannot be read → silently disable extension (no UI notification)
- If no matching server found in `/status` → fall back to what Pi knows, or use minimal display with just model identity on the right
- If `getContextUsage()` is unavailable → treat as "unknown" state; show nothing for remaining context but keep stats on left side
- No notifications emitted unless the user explicitly toggles via slash command
