export interface ChatOptions {
  prompt: string;
  sessionId?: string;
  cwd: string;
  systemPrompt?: string;
}

export interface ChatMessage {
  type: "text" | "tool_use" | "done" | "error" | "session_id";
  content?: string;
  sessionId?: string;
  tool?: string;
}

export interface AgentProvider {
  streamChat(options: ChatOptions): AsyncGenerator<ChatMessage>;
}
