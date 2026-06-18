import { tool } from "@anthropic-ai/claude-code";
import { z } from "zod";
import { flaskGet } from "../flask-client.js";

export const skillTools = [
  tool(
    "list_skills",
    "배포된 Agent 스킬 목록 조회. 스킬 이름, 버전, 활성화 상태, 할당된 Agent 유형.",
    {},
    async () => {
      const data = await flaskGet("/api/skills");
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "get_skill_detail",
    "특정 로컬 스킬의 상세 내용 조회. SKILL.md 본문, 매개변수, 출력 스키마.",
    { skill_name: z.string().describe("스킬 이름 (예: architecture, scenario-gen)") },
    async ({ skill_name }) => {
      const data = await flaskGet(`/api/skills/local/${skill_name}`);
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),
];
