# `(auto)` indicator always shows — ignores compaction setting

## Problem

The footer always appends `(auto)` to the context percentage display regardless of whether auto-compaction is actually enabled for the session. Users who disabled compaction (visible in settings UI showing `Auto-compact: false`) still saw `(auto)` in the footer.

```typescript
// before — hard-coded string literal, no conditional logic
const autoIndicator = " (auto)"; // Pi's default: auto-compaction enabled.
```

Pi's built-in `FooterComponent` tracks this behind a `_autoCompactEnabled` flag and conditionally appends `(auto)` only when true. The extension should match that behavior.

## Root cause (two levels)

**Surface:** `(auto)` was copied as a string literal when replicating the footer format, with no conditional logic.

**Deeper issue:** An intermediate fix attempt tried `agentSession?.autoCompactionEnabled`, but **`agentSession` is NOT exposed on Pi's extension context**. Extensions don't receive a reference to Pi's AgentSession object. So this always returned `undefined` and the `?? true` fallback defaulted `(auto)` on unconditionally.

## Fix

Since we can't reach into Pi's session state from an extension, read Pi's settings file directly (matching what `SettingsManager.getCompactionEnabled()` does):

```typescript
function readPiAutoCompaction(): boolean {
  const paths = [
    join(homedir(), ".pi", "settings.json"),
    join(homedir(), ".pi", "agent", "settings.json"),
  ];
  for (const p of paths) {
    try {
      if (!existsSync(p)) continue;
      const s = JSON.parse(readFileSync(p, "utf-8"));
      if (s.compaction?.enabled !== undefined)
        return Boolean(s.compaction.enabled);
    } catch {}
  }
  return true; // Pi defaults to enabled when unset
}
const autoIndicator = readPiAutoCompaction() ? " (auto)" : "";
```

Checks both `~/.pi/settings.json` and `~/.pi/agent/settings.json` since Pi deep-merges these files. Defaults to showing `(auto)` when no compaction setting is found.

## Files changed

- `footer-budget.ts` — added `readPiAutoCompaction()` helper; replaced hardcoded string with conditional check
- `docs/footer-budget-requirements.md` — updated Controls section (previously mentioned non-existent `agentSession.autoCompactionEnabled`, now describes actual settings-file read)
- `docs/footer-extension-cheatsheet.md` — pitfall #4 warns against hardcoding `(auto)` and shows correct approach

## Status

Fixed.
