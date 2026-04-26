/**
 * footer-budget — Budget-aware footer for llauncher/self-hosted workflows.
 *
 * Reads model config from /status (llauncher 0.1.1+).
 * Uses per-session effective budget: ctx_size / parallel.
 *
 * Display:
 *   ↑22k ↓2.5k R194k  73k | ████████████▌──────    Qwen3.6-35B-A3B-GGUF x2
 *                                        ^effective remaining  model parallel:2
 *
 * Usage: /footer-budget-toggle
 *        /footer-budget-off    (restore built-in)
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { truncateToWidth, visibleWidth } from "@mariozechner/pi-tui";
import * as _fs from "node:fs";
const { readFileSync } = _fs;
import * as _path from "node:path";
import * as _os from "node:os";
const { join } = _path;
const { homedir } = _os;

// ── Types ────────────────────────────────────────────────────────────────────

interface NodeCfg { host: string; port: number }

interface CacheEntry {
  runningModel: string;
  modelPort: number;
  ctxSize: number;    // total KV cache (all slots)
  parallel: number;   // concurrent session slots
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function readNodesConfig(): NodeCfg[] {
  try {
    const raw = readFileSync(join(homedir(), ".llauncher", "nodes.json"), "utf-8");
    const parsed = JSON.parse(raw);
    const values: unknown[] = Object.values(parsed);
    return values
      .filter(
        (v): v is Record<string, unknown> =>
          typeof v === "object" && v !== null && typeof (v as any).host === "string"
      )
      .map((v) => ({
        host: v.host as string,
        port: (typeof v.port === "number" && v.port > 0) ? v.port : 8765,
      }));
  } catch { return []; }
}

async function callAgent(node: NodeCfg, path: string): Promise<unknown> {
  const url = `http://${node.host}:${node.port}${path}`;
  const controller = new AbortController();
  setTimeout(() => controller.abort(), 3000);
  return fetch(url, { signal: controller.signal }).then((res) => {
    if (!res.ok) throw new Error(`${res.status} from ${node.host}`);
    return res.json();
  });
}

function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return `${(n / 1000).toFixed(1)}k`;
  if (n < 1_000_000) return `${Math.round(n / 1000)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

// ── Module-level cache ───────────────────────────────────────────────────────

let nodesConfig: NodeCfg[] = [];
let cache: CacheEntry | null = null;

function effectiveWindow(cached: CacheEntry): number {
  return cached.parallel > 0 ? Math.floor(cached.ctxSize / cached.parallel) : cached.ctxSize;
}

async function populateCache(): Promise<void> {
  // Single attempt, then one retry after 1s if it fails. No more.
  let result = await queryStatus();
  if (!result.runningModel) {
    await new Promise((r) => setTimeout(r, 1000));
    result = await queryStatus();
  }
  cache = result;
}

async function queryStatus(): Promise<CacheEntry> {
  for (const node of nodesConfig) {
    try {
      const status = await callAgent(node, "/status") as {
        running_servers: Array<{
          port: number;
          config_name: string;
          model_config?: { ctx_size?: number; parallel?: number | null };
        }>;
      };

      if (!status.running_servers?.length) continue;

      const srv = status.running_servers[0];
      const mc = srv.model_config || {};
      return {
        runningModel: srv.config_name || "",
        modelPort: srv.port || 0,
        ctxSize: mc.ctx_size ?? 0,
        parallel: (mc.parallel != null) ? mc.parallel : 1,
      };
    } catch { continue; }
  }

  return { runningModel: "", modelPort: 0, ctxSize: 0, parallel: 1 };
}

// ── Extension ────────────────────────────────────────────────────────────────

let footerEnabled = false;

export default function (pi: ExtensionAPI): void {
  nodesConfig = readNodesConfig();

  pi.on("session_start", async (_event: any, ctx: ExtensionAPI["ctx"]) => {
    if (!ctx.hasUI) return;
    nodesConfig = readNodesConfig();
    cache = null;

    const isSelfHosted = ["inference-host", "ollama", "lm-studio", "vllm"].includes(ctx.model?.provider || "");
    if (!isSelfHosted && nodesConfig.length === 0) return;

    await populateCache();
    footerEnabled = true;
    ctx.ui.setFooter(makeFooterRender(ctx));
  });

  function makeFooterRender(ctx: ExtensionAPI["ctx"]) {
    return (_tui: any, theme: any, _footerData: any) => ({
      invalidate() {},

      render(width: number): string[] {
        const c = cache as CacheEntry | null;
        const modelId = ctx.model?.id || "no-model";
        const providerLabel = ctx.model?.provider || "";

        // Effective per-session window. If we resolved it from llauncher, use that.
        // Otherwise fall back to what pi itself knows about the model.
        let contextWindow: number | null = null;
        if (c !== null && effectiveWindow(c) > 0) {
          contextWindow = effectiveWindow(c);
        } else {
          contextWindow = ctx.model?.contextWindow || null;
        }

        // ── Running totals from session branch (cosmetic, summed across all turns) ──
        let totalInput = 0, totalOutput = 0, totalCacheRead = 0;

        for (const e of ctx.sessionManager.getBranch()) {
          if (e.type === "message" && e.message.role === "assistant") {
            const u = (e as any).message?.usage || {};
            totalInput += u.input || 0;
            totalOutput += u.output || 0;
            totalCacheRead += u.cacheRead || 0;
          }
        }

        // ── Current context usage from Pi's getContextUsage() — the ground-truth for budget bar ─
        let currentTokens: number | null = null;
        let contextPercent: number | null = null;

        const agentSession = (ctx as any).agentSession;
        if (agentSession?.getContextUsage) {
          // getContextUsage() can return undefined — guard against runtime crash.
          const gu = agentSession.getContextUsage();
          if (gu && gu.tokens !== undefined) {
            currentTokens = gu.tokens;    // null means unknown (post-compact, pre-response)
            contextPercent = gu.percent;  // same — null when we can't measure
          }
        }

        const hasContextData = currentTokens !== null && currentTokens > 0;

        let remaining: number | null = null;
        if (hasContextData) {
          remaining = contextWindow! - currentTokens;
        } else if (currentTokens === 0) {
          // Fresh session — model hasn't run yet; budgetStr will handle this case.
          remaining = contextWindow;
        }

        // ── Build left stats ───────────────────────────────────────────────
        const parts: string[] = [];

        if (totalInput)   parts.push(`↑${formatTokens(totalInput)}`);
        if (totalOutput)  parts.push(`↓${formatTokens(totalOutput)}`);
        if (totalCacheRead) parts.push(`R${formatTokens(totalCacheRead)}`);

        const textFn = (s: string) => theme.fg("text", s);
        const fgFn = (s: string, color: string) => theme.fg(color, s);

        // Remaining context — just the number, no bar.
        if (contextWindow !== null && hasContextData) {
          const remainingStr = formatTokens(remaining);
          parts.push(` ${remainingStr}`);
        }

        // ── Right side: model identity ─────────────────────────────────────
        let right: string;
        if (c && c.runningModel) {
          right = c.parallel > 1 ? `${c.runningModel} x${c.parallel}` : c.runningModel;
        } else {
          // Fall back to what pi itself tracks — no budget claims, just display.
          right = [providerLabel, modelId].filter(Boolean).join(" ");
        }

        const statsLeft = parts.join(" ");

        // ── Layout ─────────────────────────────────────────────────────────
        const leftW = visibleWidth(statsLeft);
        const rightW = visibleWidth(right);
        const gap = 2;

        let statsLine: string;
        if (leftW + gap + rightW <= width) {
          const pad = " ".repeat(width - leftW - rightW);
          statsLine = `${theme.fg("dim", statsLeft)}${pad}${right}`;
        } else if (width > leftW + gap) {
          const availRight = width - leftW - gap;
          const truncated = truncateToWidth(right, availRight, "");
          statsLine = `${theme.fg("dim", statsLeft)}${" ".repeat(gap)}${truncated}`;
        } else {
          statsLine = theme.fg("dim", truncateToWidth(statsLeft, width, "..."));
        }

        return [statsLine];
      },
    });
  }

  // ── Commands ────────────────────────────────────────────────────────────────

  pi.registerCommand("footer-budget-toggle", {
    description: "Toggle budget-aware footer on/off",
    handler: async (_args: any, ctx: ExtensionAPI["ctx"]) => {
      if (footerEnabled) {
        ctx.ui.setFooter(undefined);
        footerEnabled = false;
        ctx.ui.notify("Built-in footer restored", "info");
      } else {
        nodesConfig = readNodesConfig();
        cache = null;
        await populateCache();
        footerEnabled = true;
        ctx.ui.setFooter(makeFooterRender(ctx));
      }
    },
  });

  pi.registerCommand("footer-budget-off", {
    description: "Restore built-in footer",
    handler: async (_args: any, ctx: ExtensionAPI["ctx"]) => {
      footerEnabled = false;
      ctx.ui.setFooter(undefined);
      ctx.ui.notify("Built-in footer restored", "info");
    },
  });

  pi.registerCommand("footer-budget-on", {
    description: "Enable budget-aware footer",
    handler: async (_args: any, ctx: ExtensionAPI["ctx"]) => {
      nodesConfig = readNodesConfig();
      cache = null;
      await populateCache();
      footerEnabled = true;
      ctx.ui.setFooter(makeFooterRender(ctx));
    },
  });
}
