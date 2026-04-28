/**
 * footer-budget — Replaces Pi's built-in footer so the context window total
 * comes from llauncher instead of Pi's hardcoded 128k fallback.
 *
 * Output format matches Pi exactly:
 *   ↑input ↓output RcacheRead $cost P.P%/TTTk (auto)              model-name
 * Where TTK = ctx_size / parallel from llauncher /status
 * And percentage = (tokens / effectiveWindow) × 100, recalculated with the real window.
 *
 * Usage: automatically activates on session_start for any provider.
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { truncateToWidth, visibleWidth } from "@mariozechner/pi-tui";

// ── Node.js I/O helpers (jiti-compatible destructuring) ──────────────────────

import * as _fs from "node:fs";
const { readFileSync } = _fs;
import * as _path from "node:path";
const { join } = _path;
import * as _os from "node:os";
const { homedir } = _os;

// ── Constants ────────────────────────────────────────────────────────────────

const DEFAULT_LLAUNCHER_PORT = 8765;
const NODES_FILE = join(homedir(), ".llauncher", "nodes.json");

// ── Types ────────────────────────────────────────────────────────────────────

interface CacheEntry {
  runningModel: string;
  modelPort: number;
  ctxSize: number;     // total KV cache (all slots) from llauncher /status
  parallel: number;    // concurrent session slots
}

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Token formatting — matches Pi's built-in footer exactly.
 */
function formatTokens(count: number): string {
  if (count < 1000) return String(count);
  if (count < 10_000) return `${(count / 1000).toFixed(1)}k`;
  if (count < 1_000_000) return `${Math.round(count / 1000)}k`;
  if (count < 10_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  return `${Math.round(count / 1_000_000)}M`;
}

/**
 * Compute effective per-session context window from cached llauncher data.
 */
function _effectiveWindow(entry: CacheEntry): number {
  return entry.parallel > 0 ? Math.floor(entry.ctxSize / entry.parallel) : entry.ctxSize;
}

// ── Llauncher Discovery & Status Fetch ───────────────────────────────────────

/**
 * Parse an llauncher address that may be either:
 *   - "inference-host" (bare hostname)       → http://inference-host:8765
 *   - "inference-host:8765" (host+port)     → http://inference-host:8765
 *   - "http://inference-host:8765" (full URL)→ extract host/port from URL
 * Returns { host, port } or null if unparseable.
 */
function parseLlancherAddress(raw: string): { host: string; port: number } | null {
  // Try as a full URL first.
  try {
    const url = new URL(raw);
    return { host: url.hostname, port: Number(url.port) || DEFAULT_LLAUNCHER_PORT };
  } catch {
    /* not a URL — try bare host or host:port */
  }

  if (raw.includes(":")) {
    const idx = raw.lastIndexOf(":");
    return { host: raw.slice(0, idx), port: Number(raw.slice(idx + 1)) || DEFAULT_LLAUNCHER_PORT };
  }
  return { host: raw.trim(), port: DEFAULT_LLAUNCHER_PORT };
}

/**
 * Discover llauncher hosts. Returns [{ host, port }] array.
 * Priority: $LLAUNCHER_HOST env var → ~/.llauncher/nodes.json.
 */
function discoverLluncherHosts(): Array<{ host: string; port: number }> {
  const results: Array<{ host: string; port: number }> = [];

  // Primary: environment variable (set in docker-compose.yml).
  // Accepts bare hostname, "host:port", or full URL like "http://inference-host:8765".
  const rawHost = process.env.LLAUNCHER_HOST?.trim();
  if (rawHost) {
    const parsed = parseLlancherAddress(rawHost);
    if (parsed) results.push(parsed);
  }

  // Fallback: ~/.llauncher/nodes.json for multi-node setups.
  try {
    if (results.length === 0 && _fs.existsSync(NODES_FILE)) {
      const raw = _fs.readFileSync(NODES_FILE, "utf-8");
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      for (const v of Object.values(parsed)) {
        if (
          typeof v === "object" && v !== null &&
          typeof (v as any).host === "string"
        ) {
          results.push({ host: (v as any).host, port: DEFAULT_LLAUNCHER_PORT });
        }
      }
    }
  } catch { /* ignore read errors */ }

  return results;
}

/**
 * Fetch status from a single llauncher node. Returns undefined on failure.
 */
async function fetchNodeStatus(nodeHost: string, port: number): Promise<CacheEntry | undefined> {
  const url = `http://${nodeHost}:${port}/status`;
  const controller = new AbortController();
  setTimeout(() => controller.abort(), 3_000);

  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) return undefined;
    const status = (await res.json()) as {
      running_servers: Array<{
        port: number;
        config_name: string;
        model_config?: { ctx_size?: number; parallel?: number | null };
      }>;
    };

    if (!status.running_servers?.length) return undefined;

    // Use first running server (single-node deployment assumed).
    const srv = status.running_servers[0];
    const mc = srv.model_config || {};
    return {
      runningModel: srv.config_name || "",
      modelPort: srv.port || 0,
      ctxSize: mc.ctx_size ?? 0,
      parallel: mc.parallel != null ? mc.parallel : 1,
    };
  } catch { return undefined; }
}

// ── Cache ────────────────────────────────────────────────────────────────────

let cachedEntry: CacheEntry | null = null;

async function populateCache(): Promise<void> {
  const nodes = discoverLluncherHosts();

  for (const node of nodes) {
    const entry = await fetchNodeStatus(node.host, node.port);
    if (entry && entry.ctxSize > 0) {
      cachedEntry = entry;
      return;
    }
  }

  // Nothing resolved — cache remains null. Fallback path in render() handles it.
}

// ── Extension ────────────────────────────────────────────────────────────────

export default function (pi: ExtensionAPI): void {
  pi.on("session_start", async (_event, ctx) => {
    if (!ctx.hasUI || !ctx.model) return;

    await populateCache();
    ctx.ui.setFooter(makeFooterRender(ctx));
  });

  function makeFooterRender(ctx: ExtensionAPI["ctx"]) {
    const sessionManager = (ctx as any).sessionManager;
    const agentSession = (ctx as any).agentSession;
    const modelRegistry = (ctx as any).modelRegistry;
    const stateModel = ctx.model;

    return (_tui: any, theme: any, footerData: any) => ({
      invalidate() {},

      render(width: number): string[] {
        // ── 1. Effective context window (real from llauncher, or Pi default) ─
        let effectiveWindow: number;
        if (cachedEntry && _effectiveWindow(cachedEntry) > 0) {
          effectiveWindow = _effectiveWindow(cachedEntry);
        } else {
          effectiveWindow = stateModel?.contextWindow ?? 128_000;
        }

        // ── 2. Cumulative token stats (matches Pi's getEntries() loop) ─────
        let totalInput = 0, totalOutput = 0, totalCacheRead = 0, totalCost = 0;

        if (sessionManager?.getEntries) {
          for (const entry of sessionManager.getEntries()) {
            if (entry.type === "message" && entry.message.role === "assistant") {
              const u = entry.message.usage || {};
              totalInput   += u.input ?? 0;
              totalOutput  += u.output ?? 0;
              totalCacheRead += u.cacheRead ?? 0;
              totalCost    += u.cost?.total ?? 0;
            }
          }
        }

        // ── 3. Current tokens — primary: Pi's getContextUsage().tokens (post-compact aware)
        let currentTokens: number | null = null;

        if (agentSession?.getContextUsage) {
          const gu = agentSession.getContextUsage();
          if (gu && typeof gu.tokens === "number") {
            currentTokens = gu.tokens;
          }
        }

        // Fallback: when Pi's counter is null (post-compaction boundary), estimate from
        // the session branch. Uses last assistant with real token data + char/4 heuristic.
        if (!currentTokens && currentTokens !== 0) {
          const branch = sessionManager?.getBranch();
          let totalFromLastAssistant: number | null = null;
          let lastAssistIdx: number = -1;

          // Walk backwards to find the most recent assistant with real usage data.
          for (let i = branch!.length - 1; i >= 0; i--) {
            const e = branch![i];
            if (e.type === "message" && e.message.role === "assistant") {
              const u = e.message.usage || {};
              totalFromLastAssistant =
                u.totalTokens ?? (u.input ?? 0) + (u.output ?? 0);
              lastAssistIdx = i;
              break;
            }
          }

          if (totalFromLastAssistant !== null && totalFromLastAssistant > 0) {
            // Real baseline from last API call — add char/4 for trailing user/tool messages.
            let trailingCharTokens = 0;
            for (let j = lastAssistIdx + 1; j < branch!.length; j++) {
              const e = branch![j];
              if (e.type === "message" &&
                  (e.message.role === "user" || e.message.role === "toolResult")) {
                const contentArr = Array.isArray(e.message.content)
                  ? e.message.content
                  : [{ type: "text", text: String(e.message.content ?? "") }];
                let chars = 0;
                for (const c of contentArr) {
                  if (c.type === "text" && c.text) chars += c.text.length;
                }
                trailingCharTokens += Math.ceil(chars / 4);
              }
            }
            currentTokens = totalFromLastAssistant + trailingCharTokens;
          } else {
            // No assistant with usage — rough estimate via char/4 of all text.
            let totalChars = 0;
            for (const e of branch!) {
              if (e.type === "message" &&
                  (e.message.role === "user" || e.message.role === "assistant")) {
                const contentArr = Array.isArray(e.message.content)
                  ? e.message.content
                  : [{ type: "text", text: String(e.message.content ?? "") }];
                for (const c of contentArr) {
                  if (c.type === "text" && c.text) totalChars += c.text.length;
                }
              }
            }
            currentTokens = Math.ceil(totalChars / 4);
          }
        }

        // ── 4. Build stats parts ───────────────────────────────────────────
        const statsParts: string[] = [];

        if (totalInput > 0)   statsParts.push(`↑${formatTokens(totalInput)}`);
        if (totalOutput > 0)  statsParts.push(`↓${formatTokens(totalOutput)}`);
        if (totalCacheRead > 0) statsParts.push(`R${formatTokens(totalCacheRead)}`);

        // Show cost with "(sub)" indicator if using OAuth subscription.
        const isSubscription = modelRegistry?.isUsingOAuth
          ? modelRegistry.isUsingOAuth(stateModel)
          : false;
        if (totalCost > 0 || isSubscription) {
          statsParts.push(`$${totalCost.toFixed(3)}${isSubscription ? " (sub)" : ""}`);
        }

        // ── 5. Context percentage — calculated with REAL denominator ───────
        const contextPercentValue = currentTokens != null && effectiveWindow > 0
          ? Math.min((currentTokens / effectiveWindow) * 100, 999)
          : NaN;

        const autoIndicator = " (auto)"; // Pi's default: auto-compaction enabled.

        let contextPercentDisplay: string;
        if (isNaN(contextPercentValue)) {
          // Post-compaction, pre-response — don't guess from historical totals.
          contextPercentDisplay = `?/${formatTokens(effectiveWindow)}${autoIndicator}`;
        } else {
          contextPercentDisplay = `${contextPercentValue.toFixed(1)}%/${formatTokens(effectiveWindow)}${autoIndicator}`;
        }

        // Colorize: >90% error (red), 70–90% warning (yellow), <70% default.
        let contextPercentStr: string;
        if (contextPercentValue > 90) {
          contextPercentStr = theme.fg("error", contextPercentDisplay);
        } else if (contextPercentValue >= 70) {
          contextPercentStr = theme.fg("warning", contextPercentDisplay);
        } else {
          contextPercentStr = contextPercentDisplay;
        }

        statsParts.push(contextPercentStr);

        let statsLeft = statsParts.join(" ");

        // ── 6. Model name on the right side ────────────────────────────────
        // Use llauncher's running model name if available, fallback to Pi's stateModel.id
        const hasRunningModel = !!(cachedEntry?.runningModel);
        const modelName = hasRunningModel
          ? cachedEntry!.runningModel
          : stateModel.id || "no-model";
        const providerCount = footerData.getAvailableProviderCount();

        let rightSideWithoutProvider = modelName;
        if (stateModel.reasoning) {
          // Pi's thinking level indicator on the right side.
          const thinkingLevel = (ctx as any).thinkingLevel ?? "off";
          rightSideWithoutProvider =
            thinkingLevel === "off" ? `${modelName} • thinking off` : `${modelName} • ${thinkingLevel}`;
        }

        let rightSide = rightSideWithoutProvider;
        const showProviderPrefix = providerCount > 1 && stateModel && !hasRunningModel;
        if (showProviderPrefix) {
          rightSide = `(${stateModel.provider}) ${rightSideWithoutProvider}`;
          // Don't use both provider prefix and thinking indicator simultaneously.
          if (visibleWidth(statsLeft) + 2 + visibleWidth(rightSide) > width) {
            rightSide = rightSideWithoutProvider;
          }
        }

        // ── 7. Layout — pad stats left, right-align model name ─────────────
        const statsLeftWidth = visibleWidth(statsLeft);

        let statsLine: string;
        if (statsLeftWidth > width) {
          // Stats line too wide to fit anything else.
          statsLine = truncateToWidth(statsLeft, width, "...");
        } else {
          const minPadding = 2;
          const rightSideWidth = visibleWidth(rightSide);

          if (statsLeftWidth + minPadding + rightSideWidth <= width) {
            // Both fit — add padding between stats and model name.
            const padding = " ".repeat(width - statsLeftWidth - rightSideWidth);
            statsLine = statsLeft + padding + rightSide;
          } else {
            // Right side must be truncated to fit.
            const availableForRight = width - statsLeftWidth - minPadding;
            if (availableForRight > 0) {
              const truncatedRight = truncateToWidth(rightSide, availableForRight, "");
              const paddingLen = Math.max(0, width - visibleWidth(statsLeft) - visibleWidth(truncatedRight));
              statsLine = statsLeft + " ".repeat(paddingLen) + truncatedRight;
            } else {
              // Not enough space for right side at all — just show stats.
              statsLine = statsLeft;
            }
          }
        }

        // ── 8. Apply dim to each part separately (preserves colored sections) ─
        const dimStatsLeft = theme.fg("dim", statsLeft);
        const remainder = statsLine.slice(statsLeft.length);
        const dimRemainder = theme.fg("dim", remainder);

        return [dimStatsLeft + dimRemainder];
      },
    });
  }
}
