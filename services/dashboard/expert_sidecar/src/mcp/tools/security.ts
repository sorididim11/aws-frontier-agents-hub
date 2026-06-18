import { tool } from "@anthropic-ai/claude-code";
import { z } from "zod";
import { flaskGet } from "../flask-client.js";

export const securityTools = [
  tool(
    "get_security_findings",
    "보안 분석 결과(enriched findings) 조회. 취약점, 심각도, 영향 범위, 보완 상태 포함.",
    { space_id: z.string().describe("보안 스페이스 ID") },
    async ({ space_id }) => {
      const data = await flaskGet("/api/security/insights/enriched-findings", { space_id });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "get_attack_paths",
    "공격 경로(Attack Path) 분석 결과 조회. 진입점에서 목표까지의 공격 체인.",
    { space_id: z.string().describe("보안 스페이스 ID") },
    async ({ space_id }) => {
      const data = await flaskGet("/api/security/insights/attack-paths", { space_id });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "get_defense_analysis",
    "특정 보안 발견에 대한 방어 메커니즘 분석. 기존 방어 수단과 갭 분석.",
    { finding_id: z.string().describe("보안 발견 ID (finding_id)") },
    async ({ finding_id }) => {
      const data = await flaskGet(`/api/security/insights/defense-analysis/${finding_id}`);
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),
];
