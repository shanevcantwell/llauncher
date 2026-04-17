# Enhancement: Add `repeat_penalty` configuration support

## Summary

Add support for the `repeat_penalty` parameter to model configurations in llauncher. This parameter controls the repetition of token sequences in generated text, helping prevent repetitive or monotonous output.

## Motivation

The `repeat_penalty` parameter is a standard sampling parameter in llama.cpp that allows users to control repetition in generated text. Currently, llauncher does not expose this configuration option, limiting its ability to fully configure llama-server instances.

## Current Behavior

- llauncher's `ModelConfig` model does not include a `repeat_penalty` field
- The `build_command()` function does not add `--repeat-penalty` to the server command line
- Users cannot configure this parameter via the UI or MCP tools

## Desired Behavior

- Add `repeat_penalty` field to `ModelConfig` model
- Add `--repeat-penalty` argument in `build_command()` when configured
- Support configuration persistence in `~/.llauncher/config.json`
- Make the parameter configurable via UI and MCP tools

## Configuration Details

From llama.cpp server documentation:

**Flag:** `--repeat-penalty N`

**Description:** Penalize repeat sequence of tokens

**Default value:** 1.1 (1.0 = disabled)

**Usage example:**
```bash
llama-server -m /path/to/model.gguf --repeat-penalty 1.5
```

**Reference:** https://github.com/ggml-org/llama.cpp/blob/main/tools/server/README.md

## Implementation Steps

1. Add `repeat_penalty: float | None = None` field to `ModelConfig` in `llauncher/models/config.py`
2. Add condition in `build_command()` to append `--repeat-penalty` when value is set
3. Update example config in README.md
4. Consider adding to UI forms in Manager tab

## Related Parameters

Similar sampling parameters that are already supported:
- `temperature`
- `top_k`
- `top_p`
- `min_p`
- `presence_penalty`
- `frequency_penalty`

## Testing

- Verify config persistence works with `repeat_penalty` value
- Verify server starts with `--repeat-penalty` in command line
- Verify UI displays and accepts `repeat_penalty` in forms
- Verify MCP tools can set/get `repeat_penalty`

## Notes

- Value should be validated as a positive float (e.g., `1.0` to `2.0` typical range)
- When `None` or `1.0`, the flag should not be added (disabled state)
- Consistent with other sampling parameters in the codebase