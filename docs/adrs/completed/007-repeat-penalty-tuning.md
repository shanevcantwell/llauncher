# ADR-007: Add `repeat_penalty` Configuration Support

**Status:** Accepted  
**Date:** 2026-04-26  
**Implemented:** Yes (commit ee9c90e)

## Summary

Added support for the `repeat_penalty` parameter to model configurations in llauncher. This parameter controls the repetition of token sequences in generated text, helping prevent repetitive or monotonous output.

## Implementation

### Files Modified

1. **`llauncher/models/config.py`** - Added `repeat_penalty: float | None = None` field
2. **`llauncher/core/process.py`** - Added `--repeat-penalty` flag in `build_command()`
3. **`llauncher/ui/tabs/forms.py`** - Added UI input for repeat_penalty in add/edit forms

### Design Decisions

**Default: `None` (not `1.1`)** - Follows existing pattern for optional sampling parameters. Using `None` means the flag is omitted entirely unless the user sets a value, which is consistent with other sampling params.

**UI Placement** - Inside "Advanced Options" expander, alongside other sampling parameters (`top_k`, `top_p`, `min_p`) for visual consistency.

### Tests Added

- `test_repeat_penalty_none_not_included` - Verifies flag excluded when `None`
- `test_repeat_penalty_included` - Verifies flag included with correct value
- Updated `test_full_config` fixture to include `repeat_penalty`

## Configuration

**Flag:** `--repeat-penalty N`

**Description:** Penalize repeat sequence of tokens

**Default value:** 1.1 in llama.cpp (1.0 = disabled / no penalty)

**Example:**
```bash
llama-server -m /path/to/model.gguf --repeat-penalty 1.5
```

## Related Parameters

Similar sampling parameters already supported:
- `temperature`
- `top_k`
- `top_p`
- `min_p`
