# Manual Test Checklist: Footer Race Condition Fix

## Overview
This checklist verifies the version-based invalidation fix for footer budget rendering in pi-footer-extension.

---

## Prerequisites
- [ ] llauncher running on `inference-server:8765` (or configured host)
- [ ] At least one model loaded and running via llauncher
- [ ] Pi coding agent accessible with UI enabled

---

## Test 1: Basic Footer Rendering

### Steps
1. Start a new session in Pi
2. Verify the footer appears at the bottom of the terminal
3. Check that:
   - Token counts display correctly (↑input ↓output)
   - Cache read count shows (RcacheRead if applicable)
   - Cost shows with appropriate currency symbol
   - Context percentage displays (e.g., `45.2%/128k (auto)`)
   - Model name appears on the right

### Expected Result
Footer renders correctly without errors.

---

## Test 2: Provider Switch Handled Correctly

### Steps
1. Start with provider A (e.g., "shane-pc")
2. Switch to a different provider B via Pi's model selection
3. Observe footer behavior during and after switch

### Expected Result
- Footer updates immediately when model changes
- No stale data from previous provider
- Context window calculation uses correct provider's llauncher stats

---

## Test 3: Rapid Provider Switches (Stress Test)

### Steps
1. Start a session with provider A
2. Rapidly switch between providers A and B multiple times in quick succession
3. Monitor for:
   - Footer flickering or glitching
   - Stale data persisting
   - Console errors

### Expected Result
- All switches complete cleanly
- Footer always shows current provider's data
- No race condition artifacts (duplicate renders, wrong values)

---

## Test 4: Cache Invalidation During Long Session

### Steps
1. Start a session and let it run for several minutes
2. While the session is active, trigger a footer re-render by:
   - Sending a message that triggers context usage update
   - Manually triggering model refresh if available

### Expected Result
- Footer reflects updated token counts
- No stale cache data persists
- Version-based invalidation prevents showing outdated render

---

## Test 5: Llauncher Unavailable (Failure Case)

### Steps
1. Stop the llauncher service on the configured host
2. Start a new session in Pi
3. Observe footer behavior

### Expected Result
- Footer renders with fallback data (Pi's default 128k context)
- No errors thrown to console
- Graceful degradation visible

---

## Test 6: Multi-Node Environment

### Steps
1. Configure multiple llauncher nodes in `~/.llauncher/nodes.json`
2. Start a session using each provider
3. Verify correct node is queried for each provider

### Expected Result
- Each provider maps to its configured llauncher node
- Footer shows stats from the correct node
- No cross-contamination between providers' data

---

## Test 7: Context Percentage Thresholds

### Steps
1. Start a session and build up token usage
2. Monitor footer as you approach context thresholds:
   - Below 70% (normal color)
   - 70-90% (warning color)
   - Above 90% (error color)

### Expected Result
- Color changes at correct thresholds
- Percentage displays accurately
- Auto-compaction indicator `(auto)` visible

---

## Test 8: Render Object Lifecycle

### Steps
1. Create a footer render instance
2. Simulate cache update via `populateCache()` while render exists
3. Call `invalidate()` on the existing render object

### Expected Result
- `invalidate()` detects version mismatch
- Re-renders with fresh data from new cache entry
- Old render is replaced correctly

---

## Test 9: Version Counter Behavior

### Steps
1. Observe initial `_cachedEntryVersion` (conceptually - requires debug access)
2. Trigger a successful cache populate
3. Verify version increments by 1
4. Trigger another successful populate
5. Verify version increments again

### Expected Result
- Version starts at 0
- Each successful populate increases version by 1
- Failed populate does NOT increment version

---

## Test 10: Edge Cases

### Steps
1. **Empty llauncher status**: llauncher returns empty `running_servers`
2. **Zero context window**: model_config reports ctx_size=0 or parallel=0
3. **Very large token counts**: >1M tokens to test formatting
4. **No session manager**: Simulate missing agentSession

### Expected Result
- All edge cases handled gracefully
- Fallback values used where appropriate
- No crashes or undefined behavior

---

## Debugging Notes

### Version Check
To verify version-based invalidation is working, add temporary console.log:
```typescript
// In footer-budget.ts, makeFooterRender function:
console.log(`[DEBUG] Render created with snapshotVersion=${snapshotVersion}, current=_cachedEntryVersion`);
```

### Cache State
Monitor cache state via llauncher endpoint:
```bash
curl http://inference-server:8765/status | jq .
```

### Logs
Check Pi's extension logs for footer-budget messages:
```bash
# In Pi's log directory or console output
grep -i "footer" ~/.pi/logs/*.log
```

---

## Sign-off

| Test | Status | Date | Initials |
|------|--------|------|----------|
| 1. Basic Footer Rendering | [ ] | | |
| 2. Provider Switch Handling | [ ] | | |
| 3. Rapid Switches Stress Test | [ ] | | |
| 4. Long Session Cache Invalidation | [ ] | | |
| 5. Llauncher Unavailable | [ ] | | |
| 6. Multi-Node Environment | [ ] | | |
| 7. Context Percentage Thresholds | [ ] | | |
| 8. Render Object Lifecycle | [ ] | | |
| 9. Version Counter Behavior | [ ] | | |
| 10. Edge Cases | [ ] | | |

**Overall Result:** [ ] Pass / [ ] Fail

** Tester: ** ________________________  
** Date: ** ________________________
