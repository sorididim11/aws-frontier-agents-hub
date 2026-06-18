import { tool } from "@anthropic-ai/claude-code";
import { z } from "zod";
import { flaskGet, flaskPost } from "../flask-client.js";

export const agentChatTools = [
  tool(
    "send_agent_message",
    "DevOps Agent Space에 메시지를 전송하고 응답을 받는다. Agent가 도구(kubectl, CloudWatch 등)를 사용해 답변.",
    {
      space_id: z.string().describe("Agent Space ID"),
      message: z.string().describe("Agent에게 보낼 메시지"),
      session_id: z.string().optional().describe("기존 세션 ID (미지정시 새 세션 생성)"),
    },
    async ({ space_id, message, session_id }) => {
      const data = await flaskPost("/api/agent-chat", {
        space_id,
        message,
        session_id: session_id || "",
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "start_investigation",
    "DevOps Agent에게 이상 징후 조사를 요청한다. 컨텍스트를 기반으로 Agent가 자동 조사 시작.",
    {
      space_id: z.string().describe("Agent Space ID"),
      context: z.string().describe("조사할 상황 설명 (증상, 알림 내용 등)"),
      session_id: z.string().optional().describe("기존 세션 ID"),
    },
    async ({ space_id, context, session_id }) => {
      const prompt = `다음 상황을 조사해줘. 관련 도구를 사용해서 원인을 분석하고 조치 방안을 제시해:\n\n${context}`;
      const data = await flaskPost("/api/agent-chat", {
        space_id,
        message: prompt,
        session_id: session_id || "",
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "get_agent_sessions",
    "Agent Space의 활성 채팅 세션 목록 조회.",
    { space_id: z.string().describe("Agent Space ID") },
    async ({ space_id }) => {
      const data = await flaskGet("/api/agent-sessions", { space_id });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),
];
