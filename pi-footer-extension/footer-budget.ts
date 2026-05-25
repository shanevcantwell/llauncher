/**
 * footer-budget — Replaces Pi's built-in footer so the context window total
 * comes from llama-server directly (via provider baseUrl) instead of Pi's
 * hardcoded 128k fallback.
 *
 * Output format matches Pi exactly:
 *   ↑input ↓output RcacheRead $cost P.P%/TTTk (auto)              model-name
 * Where TTK = ctx_size / parallel from llama-server /v1/models
 * And percentage = (tokens / effectiveWindow) × 100, recalculated with the real window.
 *
 * Usage: automatically activates on session_start for any provider.
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { truncateToWidth, visibleWidth } from "@mariozechner/pi-tui";

// ── Node.js I/O helpers (jiti-compatible destructuring) ──────────────────────

import * as _fs from "node:fs";
const { readFileSync, existsSync } = _fs;
import * as _path from "node:path";
const { join, dirname } = _path;
import * as _os from "node:os";
const { homedir } = _os;
import * as _child from "node:child_process";

/**
 * Lightweight JSON fetch via curl (avoids Docker ENETUNREACH on internal IPs).
 *
curl respects HTTP_PROXY env vars and routes through squid, which has a path to
192.168.137.x hosts that raw Node.js sockets cannot reach.
 */
function jsonFetch(urlStr: string, timeoutMs = 3000): Promise<any> {
  return new Promise((resolve, reject) => {
    const child = _child.spawn("curl", [
      "-s",
      "--connect-timeout", String(Math.ceil(timeoutMs / 1000)),
      "-L", urlStr,
    ]);

    let stdoutBuf = "";
    let stderrBuf = "";

    child.stdout.on("data", (chunk: Buffer) => { stdoutBuf += chunk.toString(); });
    child.stderr.on("data", (chunk: Buffer) => { stderrBuf += chunk.toString(); });

    child.on("close", (code: number | null) => {
      if (!stdoutBuf || !stdoutBuf.trim()) {
        return reject(new Error(`Empty response${stderrBuf ? ": " + stderrBuf : ""}`));
      }
      try { resolve(JSON.parse(stdoutBuf)); }
      catch { reject(new Error("Invalid JSON")); }
    });

    child.on("error", (err: Error) => {
      reject(err);
    });
  });
}

// ── Constants ────────────────────────────────────────────────────────────────

const DEFAULT_CTX_SIZE = 128_000;
const PI_AGENT_DIR = join(homedir(), ".pi", "agent");
const MODELS_JSON = join(PI_AGENT_DIR, "models.json");

// ── Types ────────────────────────────────────────────────────────────────────

interface CacheEntry {
  runningModel: string;   // actual model name from llama-server (e.g. "Qwen3.6-35B...")
  ctxSize: number;        // n_ctx from /v1/models meta
  parallel: number;       // num_parallel from /health or default 1
}

// ── Inline logger — writes to ~/.local/state/pi-extensions/footer-budget.log
//     so debug output is searchable without cluttering the TUI.              ──
const _logDir = (() => {
  const os: any = require("node:os"), pathMod: any = require("node:path");
  const d = pathMod.join(os.homedir(), ".local", "state", "pi-extensions");
  if (!_fs.existsSync(d)) _fs.mkdirSync(d, { recursive: true });
  return d;
})();
const _logFile = (() => {
  const p: any = require("node:path");
  return p.join(_logDir as string, "footer-budget.log") as unknown as string;
})();
function _writeLog(level: string, msg: string) {
  try { _fs.appendFileSync(_logFile, `[${new Date().toISOString()}] [${level}] ${msg}\n`); } catch {}
}
const log = {
  debug(m: string)   { _writeLog("DEBUG", m); },
  warn(m: string)    { _writeLog("WARN", m); },
  err(m: string)     { _writeLog("ERROR", m); },
  json(d: unknown)   { try { const ts = new Date().toISOString(); _fs.appendFileSync(_logFile, `${ts} ${JSON.stringify(d)}\n`); } catch {} }
};

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
 * Compute effective per-session context window.
 *
 * With kv-unified + cont-batching, each slot has its own full n_ctx — the
 * unified KV pool shares memory but doesn't slice sessions. Per-slot n_ctx
 * from /v1/models is always the effective window.
 */
function _effectiveWindow(entry: CacheEntry): number {
  return entry.ctxSize;
}

// ── Pi config reader ────────────────────────────────────────────────────────

interface ProviderConfig {
  baseUrl?: string;
  models?: Array<{ id: string }>;
  apiKey?: string;
  api?: string;
}

/**
 * Read ~/.pi/agent/models.json and return provider map.
 */
function readPiModels(): Record<string, ProviderConfig> | null {
  try {
    if (!existsSync(MODELS_JSON)) return null;
    const raw = _fs.readFileSync(MODELS_JSON, "utf-8");
    const parsed = JSON.parse(raw) as { providers?: Record<string, ProviderConfig> };
    return parsed.providers || null;
  } catch {
    return null;
  }
}

/**
 * Given a provider name (e.g. "inference-host-llamaserver"), extract the
 * baseUrl and port from the URL. Returns { host, port } or null.
 */
function parseProviderUrl(providerName: string): { url: string; host: string; port: number } | null {
  const providers = readPiModels();
  if (!providers || !providers[providerName]) return null;

  const baseUrl = providers[providerName].baseUrl;
  if (!baseUrl) return null;

  try {
    const url = new URL(baseUrl);
    // Strip /v1 suffix to get the base server URL for /health and /v1/models
    const baseUrlStr = `${url.protocol}//${url.host}`;
    return {
      url: baseUrl,
      host: url.hostname,
      port: Number(url.port) || (url.protocol === "https:" ? 443 : 80),
    };
  } catch {
    return null;
  }
}

// ── Fetch from llama-server directly ────────────────────────────────────────

/**
 * Fetch the list of models from a provider's baseUrl (/v1/models).
 * Returns model info with n_ctx and num_parallel if available.
 */
async function fetchModelInfo(baseUrl: string): Promise<{
  runningModel: string;
  ctxSize: number;
  parallel: number;
} | null> {
  try {
    // baseUrl comes from models.json and already contains /v1, so append /models
    const u = new URL(baseUrl);
    const baseServer = `${u.protocol}//${u.host}`;
    const modelsUrl = `${baseServer}/v1/models`;
    log.debug(`fetching ${modelsUrl}`);

    // Query /v1/models to get the active model's n_ctx from meta
    let data: { data?: Array<{ id: string; meta?: Record<string, unknown> }>; models?: Array<{ name: string }> };
    try {
      const raw = await jsonFetch(modelsUrl);
      log.json({ endpoint: "/v1/models", keys: Object.keys(raw) });
      data = raw as any;
    }
    catch (e) {
      log.err(`/v1/models failed: ${(e as Error).message}`);
      return null;
    }

    // The first model in the list is the currently loaded one.
    const model = data.data?.[0] || data.models?.[0];
    if (!model) {
      log.err(`/v1/models returned no models: ${JSON.stringify(data).slice(0, 200)}`);
      return null;
    }

    const modelId = (model as any).id || (model as any).name || "unknown";
    log.debug(`found model: ${modelId}`);

    const meta = ((model as any).meta || {}) as Record<string, unknown>;
    const ctxSize = Number(meta.n_ctx) || 0;

    // Get num_parallel from /slots endpoint (each slot = one parallel context window)
    let parallel = 1;
    try {
      const slotsUrl = `${baseServer}/slots`;
      log.debug(`fetching ${slotsUrl}`);
      const slotsData: Array<{ id: number }> | null = await jsonFetch(slotsUrl) as any;
      if (Array.isArray(slotsData)) {
        parallel = slotsData.length;
        log.debug(`/slots returned ${parallel} slot(s)`);
      }
    } catch {
      // /slots might not exist — default to 1
    }

    log.json({ model: modelId, ctxSize, parallel });
    if (!ctxSize) {
      log.err("n_ctx not found in meta");
      return null;
    }

    return { runningModel: modelId, ctxSize, parallel };
  } catch (e) {
    log.err(`fetchModelInfo error: ${(e as Error).message}`);
    return null;
  }
}

// ── Cache ────────────────────────────────────────────────────────────────────

let cachedEntry: CacheEntry | null = null;
/** Track which provider the current cache entry came from */
let _cachedProviderName: string | null = null;
// Version counter for cache invalidation detection (race condition fix)
let _cachedEntryVersion = 0;

/**
 * Populate context cache by querying llama-server directly via provider baseUrl.
 */
async function populateCache(
  targetProvider?: string,
  onComplete?: () => void
): Promise<void> {
  if (!targetProvider) {
    log.warn("populateCache called without targetProvider — defensive early return");
    return;
  }
  const providerName = targetProvider;

  // Parse the provider URL from Pi's models.json
  const parsedUrl = parseProviderUrl(providerName);
  if (!parsedUrl) {
    log.warn(`No baseUrl found for provider '${providerName}' — falling back to default.`);
    return;
  }

  const entry = await fetchModelInfo(parsedUrl.url);
  if (entry && entry.ctxSize > 0) {
    cachedEntry = entry;
    _cachedProviderName = targetProvider || null;
    _cachedEntryVersion++;
    onComplete?.();
    return;
  }

  // Nothing resolved — cache remains null. Fallback path in render() handles it.
}

// ── Extension ────────────────────────────────────────────────────────────────

export default function (pi: ExtensionAPI): void {
  pi.on("session_start", (_event, ctx) => {
    if (!ctx.hasUI || !ctx.model) return;

    // Use the initial model's provider to query llama-server.
    const provider = ctx.model.provider;
    populateCache(provider);
    ctx.ui.setFooter(makeFooterRender(ctx));

    // Safety net: retry after 5s in case of slow startup.
    setTimeout(() => {
      populateCache(provider);
    }, 5000);
  });

  pi.on("model_select", (event, ctx) => {
    if (!ctx.hasUI) return;

    const newProvider = event.model.provider;
    populateCache(newProvider);
    ctx.ui.setFooter(makeFooterRender(ctx));

    // Safety net: retry after 5s.
    setTimeout(() => {
      populateCache(newProvider);
    }, 5000);
  });

  function makeFooterRender(ctx: ExtensionAPI["ctx"]) {
    // Capture current version for invalidation detection
    const snapshotVersion = _cachedEntryVersion;
    const sessionManager = (ctx as any).sessionManager;
    const agentSession = (ctx as any).agentSession;
    const modelRegistry = (ctx as any).modelRegistry;
    const stateModel = ctx.model;

    return (_tui: any, theme: any, footerData: any) => ({
      invalidate() {
        // If cache was updated since this component was created, re-render
        if (_cachedEntryVersion !== snapshotVersion) {
          ctx.ui.setFooter(makeFooterRender(ctx));
        }
      },

      render(width: number): string[] {
        // ── 1. Effective context window (real from llama-server, or Pi default) ─

        // Invalidate cache from wrong provider — prevents stale bleed when switching providers.
        if (_cachedProviderName !== stateModel.provider) {
          log.debug(`Cache mismatch: ${_cachedProviderName} != ${stateModel.provider}, invalidating`);
          cachedEntry = null;
          _cachedProviderName = null;
        }

        let effectiveWindow: number;
        if (cachedEntry && _effectiveWindow(cachedEntry) > 0) {
          effectiveWindow = _effectiveWindow(cachedEntry);
        } else {
          effectiveWindow = stateModel?.contextWindow ?? DEFAULT_CTX_SIZE;
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

        const autoIndicator = agentSession?.autoCompactionEnabled ? " (auto)" : "";

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
        // Use llama-server's running model name if available, fallback to Pi's stateModel.id
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
        if (providerCount > 1 && stateModel) {
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
