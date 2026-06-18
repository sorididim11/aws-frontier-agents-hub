import { spawn } from "child_process";
import type { AgentProvider, ChatOptions, ChatMessage } from "./provider.js";

function stripAnsi(text: string): string {
  return text.replace(/\x1B\[[0-9;]*[a-zA-Z]/g, "").replace(/\x1B\[?[0-9;]*[a-zA-Z]/g, "");
}

function cleanKiroOutput(raw: string): string {
  let text = stripAnsi(raw);
  // Remove leading "> " prompt marker that kiro adds
  text = text.replace(/^>\s*/, "");
  return text.trim();
}

export class KiroProvider implements AgentProvider {
  private cwd: string;
  private kiroPath: string;
  private lastSessionId: string | null = null;

  constructor(cwd: string) {
    this.cwd = cwd;
    this.kiroPath = process.env.KIRO_CLI_PATH || "kiro-cli";
  }

  async warmup(): Promise<void> {
    // no-op for subprocess mode
  }

  async *streamChat(options: ChatOptions): AsyncGenerator<ChatMessage> {
    const { prompt, sessionId, cwd } = options;
    const effectiveSessionId = sessionId || this.lastSessionId;

    const agent = process.env.KIRO_AGENT || "kiro_default";
    const args = ["chat", "--no-interactive", "--trust-all-tools", "--agent", agent];
    if (effectiveSessionId) {
      args.push("--resume-id", effectiveSessionId);
    }
    args.push("--", prompt);

    try {
      const result = await this.execKiro(args, cwd);

      if (result.error) {
        yield { type: "error", content: result.error };
        return;
      }

      const text = cleanKiroOutput(result.stdout);
      if (text) {
        yield { type: "text", content: text };
      }

      // extract session ID from the run (list sessions to find latest)
      const newSessionId = await this.getLatestSessionId(cwd);
      if (newSessionId) {
        this.lastSessionId = newSessionId;
        yield { type: "session_id", sessionId: newSessionId };
      }

      yield { type: "done" };
    } catch (error: any) {
      yield { type: "error", content: error.message || "Kiro error" };
    }
  }

  private execKiro(args: string[], cwd: string): Promise<{ stdout: string; stderr: string; error?: string }> {
    return new Promise((resolve) => {
      const proc = spawn(this.kiroPath, args, {
        cwd,
        env: { ...process.env, NO_COLOR: "1", TERM: "dumb" },
        stdio: ["pipe", "pipe", "pipe"],
      });

      let stdout = "";
      let stderr = "";

      proc.stdout.on("data", (data: Buffer) => {
        stdout += data.toString();
      });
      proc.stderr.on("data", (data: Buffer) => {
        stderr += data.toString();
        if (process.env.NODE_ENV !== "production") {
          process.stderr.write(`[kiro] ${data.toString()}`);
        }
      });

      proc.on("error", (err) => {
        resolve({ stdout: "", stderr: "", error: `spawn error: ${err.message}` });
      });

      proc.on("close", (code) => {
        if (code !== 0 && !stdout.trim()) {
          resolve({ stdout, stderr, error: `kiro-cli exited with code ${code}: ${stderr.slice(-200)}` });
        } else {
          resolve({ stdout, stderr });
        }
      });

      // close stdin immediately for --no-interactive
      proc.stdin.end();
    });
  }

  private async getLatestSessionId(cwd: string): Promise<string | null> {
    return new Promise((resolve) => {
      const proc = spawn(this.kiroPath, ["chat", "--list-sessions"], {
        cwd,
        env: { ...process.env, NO_COLOR: "1", TERM: "dumb" },
        stdio: ["pipe", "pipe", "pipe"],
      });

      let output = "";
      // kiro sends list output to stderr
      proc.stdout.on("data", (d: Buffer) => { output += d.toString(); });
      proc.stderr.on("data", (d: Buffer) => { output += d.toString(); });
      proc.stdin.end();

      proc.on("close", () => {
        const cleaned = stripAnsi(output);
        const match = cleaned.match(/Chat SessionId:\s*([a-f0-9-]+)/);
        resolve(match ? match[1] : null);
      });

      proc.on("error", () => resolve(null));
    });
  }

  async shutdown(): Promise<void> {
    // no persistent process to clean up
  }
}
