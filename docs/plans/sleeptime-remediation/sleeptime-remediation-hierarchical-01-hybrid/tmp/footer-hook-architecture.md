# Extension Hook: How footer-budget.ts Plugs Into Pi's Footer Rendering

## 1. The Activation Path (ASCII flow)

```
┌────────────── session_start event ──────────────────┐
│                                                      │
│  Pi core fires "session_start"                       │
│       │                                              │
│       ▼                                              │
│  Extension receives:                                 │
│    ctx.sessionManager — for token stats              │
│    ctx.model           — model metadata              │
│    ctx.ui.setFooter()  — replaces Pi's footer        │
│       │                                              │
│       ▼                                              │
│  Extension calls ctx.ui.setFooter(                    │
│    factory(ctx)   ← returns a Component object       │
│  )                                                   │
│       │                                              │
└───────┼──────────────────────────────────────────────┘
        │
        ▼
┌────────────── TUI render loop ──────────────────────┐
│                                                      │
│  Pi's main tick calls:                               │
│    component.invalidate()   ← no-op in our case      │
│    component.render(width)  ← this draws the footer  │
│       │                                              │
│       ▼                                              │
│  Extension reads:                                    │
│    • ctx.sessionManager.getEntries() — token stats   │
│    • ctx.agentSession.getContextUsage()              │
│    • process.env.LLAUNCHER_HOST                      │
│    • llauncher /status → ctx_size, parallel          │
│       │                                              │
│       ▼                                              │
│  Extension returns [lines] array                     │
│  TUI paints them to screen                           │
│                                                      │
└──────────────────────────────────────────────────────┘
```

## 2. Pi's Default vs Extension (What Gets Replaced)

```
Pi default flow:                             Extension path:
━━━━━━━━━━━━━━━━                            ━━━━━━━━━━━━━━━━

FooterComponent                              factory(ctx):
 constructor(session, footerData)            → returns component object:
        │                                      invalidate() {}
        ▼                                      render(width):
 render(width):                               → fetches effectiveWindow from llauncher
  • session.sessionManager                     → calculates (tokens/effectiveWindow)*100
      .getEntries()                            → builds stat string with REAL denominator
    → sums input/output/cacheRead/cost          → applies same color thresholds (70/90%)
    → getContextUsage().contextWindow         → returns [line] array identical format
  → renders: ↑input ↓output RcacheRead       → model name on right stays the same
            $cost P.P%/128k (auto)   local
```

**Critical: Extensions do not patch — they replace entirely.** The extension must replicate all of Pi's footer layout logic, color theming, and padding. Only the context window math differs from Pi's built-in behavior.

## 3. What Extension Reads vs What It Provides

```
┌─────────────────── Data Sources ───────────────┐    ┌───────────────── Output ─────────────────┐
│                                                │    │                                              │
│  Session stats:           Same as Pi does      │    │  ↑input ↓output RcacheRead $cost         │
│  └ sessionManager.getEntries()                 │    │  P.P%/150k (auto)        model-name     │
│                                                │    │                                              │
│  Current tokens:       getContextUsage().tokens │    │  Where "P.P%" is RECALCULATED by:          │
│  └ ctx.agentSession.getContextUsage()         │    │                                               │
│     (post-compaction aware)                     │    │   currentTokens                          │
│                                                │    │    ─────────────── × 100 = P.P%              │
│  Context window:      llauncher /status        │    │   effectiveWindow                        │
│  └ fetch(LLAUNCHER_HOST:8765/status)          │    │                                              │
│     → ctx_size + parallel                      │    │  Where "150k" is RECALCULATED by:          │
│                                                │    │                                               │
│  Model metadata:      ctx.model                 │    │   ctx_size / parallel = effectiveWindow    │
│  └ .id, .provider, .reasoning                 │    │                                               │
└───────────────────────────────────────────────┘    └──────────────────────────────────────────────┘
```

## 4. Lifecycle Sequence (Detailed)

```
T=0:      Pi boot — reads ~/.pi/agent/settings.json → loads extensions[] list
           │
T+1:      Extension module instantiated (module-level code runs once per session)
           │
T+2:      pi.on("session_start", handler) registers callback for the current session
           │
T+3:      Session begins — event fires with ctx object bound to this session's state
           │
T+4:      Handler executes:
           1. readNodesConfig() (empty now, but prepares infrastructure)
           2. fetch llauncher /status → populate effectiveWindow cache
           3. setFooter(factory(ctx)) — REPLACES Pi's FooterComponent
           │
T+5:      TUI enters render loop — calls component.render(width) each frame (~10 FPS)
           The extension has full control over what gets drawn, how it looks, etc.
```

## 5. Why This Architecture Matters for Correctness

The extension **cannot** hook into Pi's internal calculation of `getContextUsage().percent` because that percentage is computed inside AgentSession using Pi's own context window (128k default). There's no callback or event to inject a different denominator mid-calculation.

Therefore, the correct approach is:
1. Extension computes its own percentage from raw tokens + real effective window
2. Returns a `Component` with `render()` that formats everything identically to Pi's footer
3. Returns an array of strings (single line for stats) — TUI takes over painting

This means any time Pi adds new footer elements (cost, WcacheWrite, etc.), the extension must update its replication code. The "hook" is manual replacement at a fixed insertion point in Pi's rendering pipeline.
