import { Hono } from "hono";
import { serve } from "@hono/node-server";
import { stream } from "hono/streaming";
import { execFileSync } from "child_process";
import { readFileSync, existsSync } from "fs";
import { join } from "path";
import { ClaudeCodeProvider } from "./claude-client.js";
import { KiroProvider } from "./kiro-client.js";
import { buildSystemPrompt } from "./system-prompt.js";
import { loadStore } from "./rag/index.js";
import type { AgentProvider } from "./provider.js";

const app = new Hono();

const cwd = process.env.EXPERT_CWD || process.cwd();
const systemPrompt = buildSystemPrompt(cwd);

// --- Provider discovery ---
function detectCli(cmd: string, envOverride?: string): string | null {
  if (envOverride) {
    try {
      execFileSync(envOverride, ["--version"], { encoding: "utf-8", timeout: 5000 });
      return envOverride;
    } catch {
      return null;
    }
  }
  // Use login shell to resolve PATH (picks up .zshrc/.bashrc paths)
  const shell = process.env.SHELL || "/bin/sh";
  try {
    const resolved = execFileSync(shell, ["-lc", `which ${cmd}`], {
      encoding: "utf-8",
      timeout: 5000,
    }).trim();
    if (!resolved) return null;
    execFileSync(resolved, ["--version"], { encoding: "utf-8", timeout: 5000 });
    return resolved;
  } catch {
    return null;
  }
}

interface AppConfig {
  claude?: string;
  kiro?: string;
  serverPort?: number;
}

function loadFromConfig(): AppConfig | null {
  const configPath = join(cwd, "services/dashboard/config.yaml");
  if (!existsSync(configPath)) return null;
  try {
    const raw = readFileSync(configPath, "utf-8");
    const result: AppConfig = {};

    // server.port
    const portMatch = raw.match(/server:\s*\n\s*port:\s*(\d+)/);
    if (portMatch) result.serverPort = parseInt(portMatch[1]);

    // expert.providers
    const match = raw.match(/expert:\s*\n\s*providers:\s*\n([\s\S]*?)(?=\n\w|\n$|$)/);
    if (match) {
      const claudeMatch = match[1].match(/claude:\s*\n\s*path:\s*(.+)/);
      const kiroMatch = match[1].match(/kiro:\s*\n\s*path:\s*(.+)/);
      if (claudeMatch) result.claude = claudeMatch[1].trim();
      if (kiroMatch) result.kiro = kiroMatch[1].trim();
    }

    return Object.keys(result).length > 0 ? result : null;
  } catch {
    return null;
  }
}

// Try config.yaml first, then auto-detect
const appConfig = loadFromConfig();
let claudePath: string | null;
let kiroPath: string | null;

if (appConfig) {
  // Set FLASK_API_URL from config server.port (before MCP tools load)
  if (appConfig.serverPort && !process.env.FLASK_API_URL) {
    process.env.FLASK_API_URL = `http://localhost:${appConfig.serverPort}`;
  }
  claudePath = appConfig.claude ? detectCli("claude", appConfig.claude) : detectCli("claude");
  kiroPath = appConfig.kiro ? detectCli("kiro-cli", appConfig.kiro) : detectCli("kiro-cli");
  console.log(`Loaded config (server port: ${appConfig.serverPort || "default"})`);
} else {
  claudePath = detectCli("claude", process.env.CLAUDE_CODE_PATH);
  kiroPath = detectCli("kiro-cli", process.env.KIRO_CLI_PATH);
  console.log("Auto-detected providers");
}

const available: string[] = [];
if (claudePath) available.push("claude");
if (kiroPath) available.push("kiro");

if (available.length === 0) {
  console.error("No AI CLI found. Install claude or kiro-cli.");
  process.exit(1);
}

const defaultProvider = available[0];
console.log(`Providers: [${available.join(", ")}] (default: ${defaultProvider})`);

function getProvider(name?: string): AgentProvider {
  const target = name && available.includes(name) ? name : defaultProvider;
  if (target === "kiro") {
    if (kiroPath) process.env.KIRO_CLI_PATH = kiroPath;
    return new KiroProvider(cwd);
  }
  if (claudePath) process.env.CLAUDE_CODE_PATH = claudePath;
  return new ClaudeCodeProvider();
}

// --- Routes ---
app.get("/health", (c) =>
  c.json({ ok: true, cwd, providers: available, default: defaultProvider })
);

app.post("/api/chat", async (c) => {
  const body = await c.req.json<{
    prompt: string;
    sessionId?: string;
    provider?: string;
    pageContext?: Record<string, unknown>;
  }>();
  const { prompt, sessionId, pageContext } = body;

  if (!prompt?.trim()) {
    return c.json({ error: "prompt required" }, 400);
  }

  const activeProvider = getProvider(body.provider);

  let enrichedPrompt = prompt.trim();
  if (pageContext && Object.keys(pageContext).length > 0) {
    const ctx = `[현재 페이지 컨텍스트: ${JSON.stringify(pageContext)}]\n\n`;
    enrichedPrompt = ctx + enrichedPrompt;
  }

  return stream(c, async (s) => {
    const messages = activeProvider.streamChat({
      prompt: enrichedPrompt,
      sessionId: sessionId || undefined,
      cwd,
      systemPrompt,
    });

    for await (const msg of messages) {
      await s.write(JSON.stringify(msg) + "\n");
    }
  });
});

// Load RAG embeddings if available
const sidecarDir = new URL(".", import.meta.url).pathname;
const ragPath = join(sidecarDir, "../data/embeddings.json");
loadStore(ragPath);

const sidecarPort = (() => {
  if (process.env.SIDECAR_PORT) return parseInt(process.env.SIDECAR_PORT);
  const configPath = join(cwd, "services/dashboard/config.yaml");
  if (existsSync(configPath)) {
    const raw = readFileSync(configPath, "utf-8");
    const m = raw.match(/sidecar_port:\s*(\d+)/);
    if (m) return parseInt(m[1]);
  }
  return 3100;
})();
console.log(`Expert sidecar listening on :${sidecarPort} (cwd: ${cwd})`);
serve({ fetch: app.fetch, port: sidecarPort });
