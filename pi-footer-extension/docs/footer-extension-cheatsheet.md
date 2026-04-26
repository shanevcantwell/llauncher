# Pi Footer Extension Cheat Sheet

## How Footer Rendering Works in Pi

Pi's TUI uses a component-based rendering pipeline. Every UI element — header, footer, body, overlays — implements the `Component` interface from `@mariozechner/pi-tui`:

```typescript
interface Component {
    render(width: number): string[];   // one string per terminal row
    invalidate(): void;                // refresh signal (e.g., theme change)
}
```

**Output is a flat array of strings.** There is no virtual DOM, no tree structure, no AST — just plain text lines painted top-to-bottom to the terminal. The `width` parameter is the current viewport width in columns.

---

## Extension Hook: Replacing Pi's Built-in Footer

### The Activation Point

Pi fires a `"session_start"` event on every new session, providing an extension context (`ctx`). Extensions tap this to call `ctx.ui.setFooter()`, which **completely replaces** Pi's built-in footer component. There is no patching or decoration mechanism — your extension becomes the sole source of truth for what gets painted at the bottom.

```typescript
pi.on("session_start", async (_event, ctx) => {
    if (!ctx.hasUI) return;

    // setFooter() accepts a factory that returns a Component object
    ctx.ui.setFooter((tui, theme, footerData) => ({
        invalidate() { /* no-op */ },

        render(width: number): string[] {
            // Build and return lines here.
            // Pi passes `theme` (for colorizing) and `footerData` (for git/provider info).
            return [myLine];
        }
    }));
});
```

### Why "Completely Replace" Matters

- Your extension must replicate all of Pi's footer formatting: token stats, percentage display, padding/spaces, model name on the right side.
- If Pi adds new footer elements (cost column, cache write stats, thinking level indicator), your extension needs manual updates to keep up.
- You're copy-and-adapt, not patch-and-go.

---

## The Render Pipeline

```
Raw data sources                      Build steps                               Painted output

ctx.sessionManager.getEntries()  ──►   for entry → formatTokens(entry.usage)     │
ctx.agentSession.getContextUsage() ──► ├──── build stat string                    │→ "↑38k ↓11k R389k $0.002 20.2%/128k (auto)" + padding + "local"
Provider model-registry defaults   ──►  └──── P.P% / TTTk (auto) [colorized]      │

FooterDataProvider.getGitBranch()         appended to pwd line above stats line
FooterDataProvider.getExtensionStatuses() joined on third line if non-empty
```

---

## Available Data Sources in Extension Context

### From `ctx` (extension context object)

| Property | Type | Purpose |
|----------|------|---------|
| `ctx.model` | `{ id: string, provider: string, contextWindow?: number, reasoning?: boolean }` | Model metadata and name |
| `ctx.sessionManager.getEntries()` | `Entry[]` | All session entries (includes historical data across compactions) |
| `ctx.sessionManager.getBranch()` | `Entry[]` | Current branch only (post-compaction pruned list) |
| `ctx.ui.setFooter(factory)` | function | Replaces Pi's footer entirely with your Component |
| `(ctx as any).agentSession.getContextUsage()` | `{ tokens, percent } \| null` | Post-compaction-aware context usage. Returns `null` briefly after compaction before next LLM response. |
| `(ctx as any).thinkingLevel` | `"off" \| "low" \| "medium" \| "high"` | Current thinking level (for right-side display) |

### From `footerData` parameter to factory function

| Method | Return type | Purpose |
|--------|-------------|---------|
| `getGitBranch()` | `string \| null` | Current git branch or `"detached"` |
| `getExtensionStatuses()` | `ReadonlyMap<string, string>` | Extension-registered status messages (keyed by module name) |
| `getAvailableProviderCount()` | `number` | Number of providers with available models |

**These are raw values** — no objects or builders. You fetch them and concatenate into your strings yourself.

---

## Text Formatting & Colorization

### Token formatting (must replicate Pi's logic)

```typescript
function formatTokens(n: number): string {
    if (n < 1000) return String(n);
    if (n < 10_000) return `${(n / 1000).toFixed(1)}k`;   // e.g., "3.2k"
    if (n < 1_000_000) return `${Math.round(n / 1000)}k`; // e.g., "389k"
    return `${(n / 1_000_000).toFixed(1)}M`;              // e.g., "2.5M"
}
```

### Colorization (ANSI escape sequences via `theme.fg`)

`theme` is a parameter to your factory function. It has one method:

```typescript
// Pure string decoration — wraps text in ANSI color + reset codes and returns the result.
theme.fg("error", "95%")     → "\x1b[31m95%\x1b[0m"  (red)
theme.fg("warning", "72%")   → "\x1b[33m72%\x1b[0m"  (yellow)
theme.fg("dim", statsLeft)   → "\x1b[2m↑38k ↓...\x1b[0m"
```

Color thresholds match Pi's built-in footer:

| Usage | Color | Code |
|-------|-------|------|
| >90% | red | `theme.fg("error", str)` |
| 70–90% | yellow | `theme.fg("warning", str)` |
| <70% | default (white) | plain string, no colorization |

### Spacing & Layout (manual calculation)

There is **no layout engine** — you calculate widths and insert spaces yourself.

```typescript
import { visibleWidth } from "@mariozechner/pi-tui";

const leftText = "↑38k ↓11k R389k 20.2%/128k (auto)";
const rightText = "local";

// visibleWidth() strips ANSI escape sequences to get the true display width
const padSize = width - visibleWidth(leftText) - 2 - visibleWidth(rightText);
if (padSize > 0) {
    return [theme.fg("dim", leftText) + " ".repeat(padSize) + rightText];
} else {
    // Truncate: import truncateToWidth from @mariozechner/pi-tui as well.
    const truncatedRight = truncateToWidth(rightText, width - visibleWidth(leftText) - 2);
    return [theme.fg("dim", leftText) + " ".repeat(2) + truncatedRight];
}
```

---

## Example: Minimal Working Footer Extension

```typescript
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { truncateToWidth, visibleWidth } from "@mariozechner/pi-tui";

function formatTokens(n: number): string {
    if (n < 1000) return String(n);
    if (n < 10_000) return `${(n / 1000).toFixed(1)}k`;
    if (n < 1_000_000) return `${Math.round(n / 1000)}k`;
    return `${(n / 1_000_000).toFixed(1)}M`;
}

export default function (pi: ExtensionAPI): void {
    pi.on("session_start", (_event, ctx) => {
        if (!ctx.hasUI) return;

        const session = (ctx as any).sessionManager;
        const modelId = ctx.model?.id || "no-model";

        ctx.ui.setFooter((_tui, theme, footerData) => ({
            invalidate() {},

            render(width: number): string[] {
                // Compute token stats from session entries
                let input = 0, output = 0;
                for (const entry of session.getEntries()) {
                    if (entry.type === "message" && entry.message.role === "assistant") {
                        const u = entry.message.usage || {};
                        input += u.input || 0;
                        output += u.output || 0;
                    }
                }

                // Build stats line
                const parts: string[] = [];
                if (input)   parts.push(`↑${formatTokens(input)}`);
                if (output)  parts.push(`↓${formatTokens(output)}`);
                parts.push(`${ctx.model?.provider || ""} ${modelId}`);

                // Layout: pad to right-align model name
                const leftW = visibleWidth(parts.join(" "));
                const gap = Math.max(0, width - leftW - 2 - visibleWidth(modelId) - 2);
                const padded = parts.slice(0, parts.length - 1).join(" ");
                const rightSide = (parts as any)[parts.length - 1];

                let line;
                if (gap >= 2) {
                    line = padded + " ".repeat(gap) + rightSide;
                } else {
                    line = truncateToWidth(parts.join(" "), width, "...");
                }

                return [theme.fg("dim", line)];
            }
        }));
    });
}
```

---

## Common Pitfalls

### 1. ANSI codes in `visibleWidth()` / `truncateToWidth()`
If you call these before colorizing, the widths are wrong because escape sequences inflate the length. Always measure plain text first, insert into layout, then apply colors.

### 2. Context window denominator vs numerator confusion
Pi's built-in footer uses `getContextUsage().contextWindow` for both percentage calculation and display total — but Pi defaults unknown providers to **128,000**. If your model has a different real context window (e.g., loaded through llauncher with `ctx_size / parallel = 400k/3 ≈ 133k`), the percentage will be wrong. Solution: compute your own denominator from authoritative source and recalculate percentage as `(tokens / yourWindow) × 100`.

### 3. getContextUsage() returns null post-compaction
After a compaction event, `getContextUsage()` returns `{ tokens: null, percent: null }` until the next LLM response confirms actual token counts. During this window, display `"?/TTTk (auto)"` rather than guessing from accumulated session totals — those historical sums are unbounded and misleading.

### 4. getEntries() vs getBranch()
- `getEntries()` iterates ALL history including past compaction entries → **unboundedly growing**, not useful for current usage
- `getBranch()` iterates only the current post-compaction message list → pruned, but each entry still carries its original API response token counts

For token stats (↑/↓/R), both behave similarly because footer extensions have no access to the real in-flight prompt count — only API-reported totals. For budget math, prefer `getContextUsage().tokens` which uses post-compaction message estimates.

---

## Quick Reference: Footer Line Structure

Pi's built-in footer renders **three lines**:

```
~ (pwd)                                                          local        ← line 1: pwd + model name
↑38k ↓11k R389k $0.002 20.2%/128k (auto)                          ← line 2: stats + context percentage
<extension statuses>                                              ← line 3: optional, from getExtensionStatuses()
```

Extensions control **all three lines** via `render(): string[]`. The convention is to omit the pwd line if your extension replaces the entire footer — but you could include it for completeness. Model name always appears right-aligned on the stats line (or pwd line in Pi's default).
