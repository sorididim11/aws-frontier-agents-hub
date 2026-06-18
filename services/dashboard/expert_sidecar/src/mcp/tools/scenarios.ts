import { tool } from "@anthropic-ai/claude-code";
import { z } from "zod";
import { flaskGet, flaskPost } from "../flask-client.js";

export const scenarioTools = [
  tool(
    "list_scenarios",
    "스페이스의 시나리오 목록 조회. 장애 시나리오 이름, 유형, 생성일, 상태 포함.",
    { space_id: z.string().describe("스페이스 ID") },
    async ({ space_id }) => {
      const data = await flaskGet("/api/scenarios", { space_id });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "get_run_status",
    "특정 시나리오 실행(run)의 현재 상태 조회. 단계별 진행도, 성공/실패, 에러 메시지.",
    { run_id: z.string().describe("실행 ID (run_id)") },
    async ({ run_id }) => {
      const data = await flaskGet(`/api/scenario-run/${run_id}/status`);
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "list_active_runs",
    "현재 실행 중인 시나리오 목록 조회.",
    {},
    async () => {
      const data = await flaskGet("/api/active-runs");
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "get_evaluation",
    "시나리오 실행 후 평가 결과 조회. 점수, 평가 항목, 개선 제안 포함.",
    { run_id: z.string().describe("실행 ID (run_id)") },
    async ({ run_id }) => {
      const data = await flaskGet(`/api/evaluate/${run_id}`);
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "generate_scenario",
    "AI를 활용해 장애 시나리오를 생성 요청. 템플릿과 앱 정보 기반으로 시나리오 구성.",
    {
      space_id: z.string().describe("스페이스 ID"),
      message: z.string().describe("시나리오 생성 요청 메시지 (어떤 장애를 시뮬레이션할지)"),
      template_id: z.string().optional().describe("사용할 템플릿 ID"),
      app_name: z.string().optional().describe("대상 앱 이름"),
    },
    async ({ space_id, message, template_id, app_name }) => {
      const body: Record<string, unknown> = { space_id, message };
      if (template_id) body.template_id = template_id;
      if (app_name) body.app_name = app_name;
      const data = await flaskPost("/api/scenario-chat", body);
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "review_scenario",
    "시나리오의 완성도와 실행 가능성을 검토. 검증 규칙 기반 분석.",
    { scenario_id: z.string().describe("시나리오 ID") },
    async ({ scenario_id }) => {
      const data = await flaskGet(`/api/scenario-validate/${scenario_id}`);
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),
];
