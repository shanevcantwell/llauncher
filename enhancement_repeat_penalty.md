# Enhancement: Add `repeat_penalty` configuration support

## Summary

Add support for the `repeat_penalty` parameter to model configurations in llauncher. This parameter controls the repetition of token sequences in generated text, helping prevent repetitive or monotonous output.

## Motivation

The `repeat_penalty` parameter is a standard sampling parameter in llama.cpp that allows users to control repetition in generated text. Currently, llauncher does not expose this configuration option, limiting its ability to fully configure llama-server instances.

## Current Behavior

- `ModelConfig` in `llauncher/models/config.py` does not include a `repeat_penalty` field
- `build_command()` in `llauncher/core/process.py` does not add `--repeat-penalty` to the server command line
- UI forms in `llauncher/ui/tabs/forms.py` have no repeat_penalty input (add or edit forms)
- Users cannot configure this parameter via the UI or MCP tools
- No tests cover `--repeat-penalty` flag generation

## Desired Behavior

- Add `repeat_penalty: float | None` field to `ModelConfig`
- Add `--repeat-penalty` argument in `build_command()` when value is set
- Support configuration persistence in `~/.llauncher/config.json` (automatic via Pydantic serialization)
- Make the parameter configurable via UI (Add/Edit model forms)
- MCP tools auto-expose via `get_model_config` → `config.to_dict()` — no extra work needed

## Configuration Details

From llama.cpp server documentation:

**Flag:** `--repeat-penalty N`

**Description:** Penalize repeat sequence of tokens

**Default value:** 1.1 in llama.cpp (1.0 = disabled / no penalty)

**Usage example:**
```bash
llama-server -m /path/to/model.gguf --repeat-penalty 1.5
```

**Reference:** https://github.com/ggml-org/llama.cpp/blob/main/tools/server/README.md

## Design Decisions

### Default: `None` (not `1.1`)

The existing codebase pattern for optional sampling parameters is `float | None = None` (e.g., `temperature`, `top_k`, `top_p`). Setting a default of `1.1` would mean *every* model always passes `--repeat-penalty 1.1` to the server, which differs from the "only pass when user explicitly sets it" pattern used throughout. Using `None` as the default means the flag is omitted entirely unless the user sets a value, which is consistent and avoids changing behavior for existing configs.

### No `field_validator`

The existing codebase does not use `field_validator` for sampling parameters — `temperature`, `top_p`, `min_p`, etc. are plain `float | None` with no validation. We follow the same pattern. Users who need guidance can rely on the UI help text and the `step` parameter.

### UI Placement

The "Manager tab" (`llauncher/ui/tabs/manager.py`) is a stub that redirects to the Dashboard. All form work goes in `llauncher/ui/tabs/forms.py`, inside the "Advanced Options" expander, placed alongside the other sampling parameters (`top_k`, `top_p`, `min_p`) for visual consistency.

## Files to Change

### 1. `llauncher/models/config.py`

Add one field to `ModelConfig`:

```python
repeat_penalty: float | None = None
```

Place it in the "Sampling parameters" section, between `min_p` and `reverse_prompt`, matching the grouping of existing parameters.

### 2. `llauncher/core/process.py`

In `build_command()`, add one conditional block alongside the other sampling parameters:

```python
if config.repeat_penalty is not None:
    cmd.extend(["--repeat-penalty", str(config.repeat_penalty)])
```

Place it after the `min_p` block, matching the model field ordering.

### 3. `llauncher/ui/tabs/forms.py` — 4 functions

This file is the most impactful. All four functions in this file need updates:

**a) `render_add_model()`** — Add the UI input:
- After the `min_p` column (end of `col_adv6/7/8`), add a new `st.number_input` for `repeat_penalty`
- Use `min_value=0.0`, `value=1.1`, `step=0.01` (default matches llama.cpp's default)
- Include help text: `"Penalize repeat sequences of tokens (1.0 = disabled)"`

**b) `_process_add_model()`** — Accept and pass the value:
- Add `repeat_penalty: float` parameter to function signature
- Add `repeat_penalty` to docstring args
- Add `repeat_penalty=repeat_penalty if repeat_penalty > 0 else None` to the `ModelConfig()` constructor call

**c) `render_edit_model()`** — Add the UI input:
- After the `min_p` column, add the same `st.number_input` pattern
- Pre-fill with `value=config.repeat_penalty or 1.1` (default to 1.1 for existing configs with no value)
- Include help text

**d) `_process_edit_model()`** — Accept, pass, and apply:
- Add `repeat_penalty: float` parameter to function signature
- Add `repeat_penalty` to docstring args
- Add `repeat_penalty` to the call from `render_edit_model()`
- Add `repeat_penalty` to `config.model_copy(update={...})`

### 4. `tests/unit/test_process.py`

**a) Update `full_config` fixture** — Add `repeat_penalty` so the existing `test_full_config()` test validates it:
```python
"repeat_penalty": 1.5,
```

**b) New test:** `test_repeat_penalty_none_not_included`
- Set `repeat_penalty = None` on a config
- Verify `--repeat-penalty` does NOT appear in the command

**c) New test:** `test_repeat_penalty_included`
- Set `repeat_penalty = 1.5` on a config
- Verify `--repeat-penalty` IS in the command with value `"1.5"`

### 5. `README.md`

Add `repeat_penalty` to the example config JSON:
```json
"repeat_penalty": null,
```

Place between `"min_p"` and `"reverse_prompt"` to match model field ordering.

## What Needs NO Changes

| Component | Reason |
|---|---|
| `MCP tools` | `get_model_config` calls `config.to_dict()` — field is auto-included |
| `model_card.py` | Model cards do not display sampling params; repeat_penalty is visible only in edit form |
| `ConfigStore` | Pydantic serialization handles persistence automatically |
| `ChangeRules` | No access control implications |
| `remote/` modules | Remote node state syncs via `to_dict()` |

## Related Parameters

Similar sampling parameters already supported in the same location:
- `temperature`
- `top_k`
- `top_p`
- `min_p`

Note: `presence_penalty` and `frequency_penalty` exist in llama.cpp but are not yet exposed by llauncher.

## Testing

| Test | Type | Location |
|---|---|---|
| `test_repeat_penalty_none_not_included` | Unit: `build_command` excludes flag when `None` | `tests/unit/test_process.py` |
| `test_repeat_penalty_included` | Unit: `build_command` includes flag with correct value | `tests/unit/test_process.py` |
| `test_full_config` | Unit: existing test validates fixture includes `repeat_penalty` | `tests/unit/test_process.py` |
| Config persistence | Manual/Integration: verify `repeat_penalty` round-trips through `config.json` | Manual |
| UI add form | Manual: verify input appears, accepts values, saves to config | Manual |
| UI edit form | Manual: verify input pre-fills, accepts changes, saves | Manual |
| MCP `get_model_config` | Manual: verify `repeat_penalty` appears in returned dict | Manual |
