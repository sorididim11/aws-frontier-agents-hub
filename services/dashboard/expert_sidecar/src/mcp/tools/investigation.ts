import { tool } from "@anthropic-ai/claude-code";
import { z } from "zod";
import { flaskGet } from "../flask-client.js";

export const investigationTools = [
  tool(
    "get_investigation_journal",
    "조사 저널 조회. Agent의 가설, 발견, 인과 체인을 구조화하여 반환.",
    {
      space_id: z.string().describe("스페이스 ID"),
      task_id: z.string().describe("조사 태스크 ID"),
    },
    async ({ space_id, task_id }) => {
      const data = await flaskGet("/api/investigation-journal", { space_id, task_id });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "verify_dag",
    "Investigation DAG 구조 검증. 관찰→발견→근본원인 체인의 완결성 확인.",
    {
      space_id: z.string().describe("스페이스 ID"),
      task_id: z.string().describe("조사 태스크 ID"),
    },
    async ({ space_id, task_id }) => {
      const data = await flaskGet("/api/dag-verify", { space_id, task_id });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "get_investigation_history",
    "최근 조사 이력 목록 조회.",
    {
      space_id: z.string().describe("스페이스 ID"),
      limit: z.number().optional().describe("최대 조회 수 (기본 10)"),
    },
    async ({ space_id, limit }) => {
      const params: Record<string, string> = { space_id };
      if (limit) params.limit = String(limit);
      const data = await flaskGet("/api/history", params);
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),
];
