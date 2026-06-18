import { query } from "@anthropic-ai/claude-code";
import type { AgentProvider, ChatOptions, ChatMessage } from "./provider.js";
import { overviewMcpServer } from "./mcp/index.js";

export class ClaudeCodeProvider implements AgentProvider {
  async *streamChat(options: ChatOptions): AsyncGenerator<ChatMessage> {
    const { prompt, sessionId, cwd, systemPrompt } = options;
    const stderrLines: string[] = [];

    try {
      const result = query({
        prompt,
        options: {
          cwd,
          permissionMode: "bypassPermissions",
          mcpServers: { "overview-app": overviewMcpServer },
          ...(sessionId && { resume: sessionId }),
          ...(systemPrompt && { customSystemPrompt: systemPrompt }),
          maxTurns: 20,
          pathToClaudeCodeExecutable:
            process.env.CLAUDE_CODE_PATH || undefined,
          stderr: (data: string) => {
            stderrLines.push(data);
            if (process.env.NODE_ENV !== "production") {
              process.stderr.write(`[claude] ${data}\n`);
            }
          },
        },
      });

      for await (const message of result) {
        if (message.type === "assistant") {
          const content = (message as any).message?.content;
          if (Array.isArray(content)) {
            for (const block of content) {
              if (block.type === "text") {
                yield { type: "text", content: block.text };
              } else if (block.type === "tool_use") {
                yield { type: "tool_use", tool: block.name, content: JSON.stringify(block.input) };
              }
            }
          }
        } else if (message.type === "result") {
          yield { type: "session_id", sessionId: message.session_id };
          yield { type: "done" };
        }
      }
    } catch (error: any) {
      const stderr = stderrLines.join("\n").slice(-500);
      yield {
        type: "error",
        content: `${error.message || "Unknown error"}${stderr ? `\n[stderr] ${stderr}` : ""}`,
      };
    }
  }
}
