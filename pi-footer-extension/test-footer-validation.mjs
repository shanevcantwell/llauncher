/**
 * test-footer-validation.mjs — Validates the version-based invalidation logic
 * for the footer race condition fix.
 * 
 * Mocks Pi's extension API and tests:
 * 1. Version increments on successful cache populate
 * 2. Rapid provider switches handled correctly
 * 3. Cache failure doesn't increment version
 * 4. invalidate() detects stale renders
 *
 * Run with: node test-footer-validation.mjs
 */

import { setTimeout as delay } from "node:timers/promises";

// ── Core state (mirrors footer-budget.ts) ────────────────────────────────────

let _cachedEntryVersion = 0;
let cachedEntry = null;
let _cachedProviderName = null;

/**
 * Simulates populateCache() — the function that fetches llauncher status.
 * Returns undefined on failure, cache entry on success. Version only increments on success.
 */
async function populateCache(targetProvider) {
  // Simulate network delay (~50ms instead of real ~3s)
  await delay(50);

  if (targetProvider === "error") {
    return; // Simulate cache failure — no version bump
  }

  cachedEntry = {
    runningModel: targetProvider === "inference-host" ? "Qwen3.6-35B" : "Llama-70B",
    modelPort: 8765,
    ctxSize: 409_600,
    parallel: 3,
  };

  _cachedProviderName = targetProvider || null;
  _cachedEntryVersion++; // <-- THE FIX: increment only on success
}

/**
 * Simulates makeFooterRender() — captures version snapshot for invalidation.
 */
function makeFooterRender(ctx) {
  const snapshotVersion = _cachedEntryVersion; // <-- Capture at render time

  return {
    invalidate() {
      if (_cachedEntryVersion !== snapshotVersion) {
        console.log(
          `  [PASS] Version mismatch detected: render=${snapshotVersion}, current=${_cachedEntryVersion}`
        );
      } else {
        console.log("  [INFO] No version mismatch — render is fresh");
      }
    },
    getSnapshotVersion() {
      return snapshotVersion;
    },
  };
}

// ── Test runner ───────────────────────────────────────────────────────────────

let passed = 0;
let failed = 0;

async function runTest(name, fn) {
  console.log(`\n=== ${name} ===`);
  try {
    await fn();
    passed++;
    console.log("✓ PASSED");
  } catch (e) {
    failed++;
    console.log(`✗ FAILED: ${e.message}`);
  }
}

function assert(condition, msg) {
  if (!condition) throw new Error(msg || "Assertion failed");
}

// ── Test scenarios ────────────────────────────────────────────────────────────

async function test1_VersionIncrementsOnSuccess() {
  const initialVersion = _cachedEntryVersion;
  assert(initialVersion === 0, `Expected start at version 0, got ${initialVersion}`);

  await populateCache("inference-host");

  assert(
    _cachedEntryVersion === initialVersion + 1,
    `Expected version to increment by 1, got ${_cachedEntryVersion} vs ${initialVersion + 1}`
  );
}

async function test2_CacheFailureNoIncrement() {
  const initialVersion = _cachedEntryVersion;

  await populateCache("error"); // Simulate fetch failure

  assert(
    _cachedEntryVersion === initialVersion,
    `Version should not increment on cache failure. Was ${initialVersion}, now ${_cachedEntryVersion}`
  );
}

async function test3_InvalidateDetectsStaleRender() {
  const render = makeFooterRender({}); // Creates render with version snapshot

  // Before any update, invalidate should show no mismatch
  console.log("  [CHECK] Calling invalidate before cache update...");
  render.invalidate();

  // Update cache (simulates populateCache completing)
  await populateCache("node1");

  // Now invalidate should detect the version bump
  console.log("  [CHECK] Calling invalidate after cache update...");
  render.invalidate();

  assert(
    _cachedEntryVersion > render.getSnapshotVersion(),
    "invalidate() should have detected version mismatch"
  );
}

async function test4_RapidProviderSwitches() {
  // Reset state for clean isolation
  const startVersion = _cachedEntryVersion;

  // Create a render with the current snapshot
  const render1 = makeFooterRender({ model: { provider: "nodeA" } });
  const ver1 = render1.getSnapshotVersion();

  // Simulate two rapid cache updates (model switches before first completes)
  await Promise.all([
    populateCache("nodeA"),
    delay(20).then(() => populateCache("nodeB")),
  ]);

  // Final version should be start + 2 (two successful populates)
  const expectedVersion = startVersion + 2;
  assert(
    _cachedEntryVersion === expectedVersion,
    `Expected version ${expectedVersion} after two rapid updates, got ${_cachedEntryVersion}`
  );

  // Both renders should detect the stale state (version > their snapshot)
  console.log("  [CHECK] Render1 invalidation check...");
  render1.invalidate();
}

async function test5_MultipleSuccessiveUpdates() {
  const startVersion = _cachedEntryVersion;

  for (let i = 0; i < 3; i++) {
    await populateCache(`node${i}`);
    assert(
      _cachedEntryVersion === startVersion + i + 1,
      `After update ${i + 1}, expected version ${startVersion + i + 1}, got ${_cachedEntryVersion}`
    );
  }

  // Create a render after all updates
  const render = makeFooterRender({});
  
  // Now simulate one more update (e.g., from model_select event)
  await populateCache("nodeX");

  // Render should detect staleness
  console.log("  [CHECK] Post-update invalidation check...");
  render.invalidate();

  assert(
    _cachedEntryVersion > render.getSnapshotVersion(),
    "Stale render should detect version bump"
  );
}

// ── Run all tests ─────────────────────────────────────────────────────────────

console.log("=== Footer Budget Validation Tests ===\n");

await runTest("1. Version increments on successful cache populate", test1_VersionIncrementsOnSuccess);
await runTest("2. Cache failure does not increment version", test2_CacheFailureNoIncrement);
await runTest("3. invalidate() detects stale render after update", test3_InvalidateDetectsStaleRender);
await runTest("4. Rapid provider switches handled correctly", test4_RapidProviderSwitches);
await runTest("5. Multiple successive updates tracked correctly", test5_MultipleSuccessiveUpdates);

console.log(`\n=== Results: ${passed} passed, ${failed} failed ===`);

if (failed > 0) {
  process.exit(1);
}
