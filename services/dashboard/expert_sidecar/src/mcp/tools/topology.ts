import { tool } from "@anthropic-ai/claude-code";
import { z } from "zod";
import { flaskGet } from "../flask-client.js";

export const topologyTools = [
  tool(
    "get_topology",
    "현재 스페이스의 아키텍처 토폴로지(노드/엣지) 조회. 서비스 간 연결 관계와 데이터 흐름을 보여줌.",
    { space_id: z.string().describe("스페이스 ID") },
    async ({ space_id }) => {
      const data = await flaskGet("/api/arch/topology", { space_id });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "get_arch_view",
    "레벨별(L1/L2/L3) 아키텍처 뷰 조회. L1=서비스 레벨, L2=컴포넌트 레벨, L3=리소스 뷰.",
    {
      space_id: z.string().describe("스페이스 ID"),
      level: z.enum(["L1", "L2", "L3"]).describe("아키텍처 레벨"),
      app: z.string().optional().describe("특정 앱 그룹 필터 (L2/L3에서 사용)"),
    },
    async ({ space_id, level, app }) => {
      const params: Record<string, string> = { space_id, level };
      if (app) params.app = app;
      const data = await flaskGet("/api/arch/view", params);
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "get_arch_status",
    "아키텍처 분석 상태 조회. 현재 분석 진행 여부, 완료된 레이어, 에러 메시지 등.",
    {
      space_id: z.string().describe("스페이스 ID"),
      include_topology: z.boolean().optional().describe("토폴로지 데이터 포함 여부"),
    },
    async ({ space_id, include_topology }) => {
      const params: Record<string, string> = { space_id };
      if (include_topology) params.include_topology = "true";
      const data = await flaskGet("/api/arch/status", params);
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "get_k8s_view",
    "K8s 네임스페이스별 상세 뷰 조회. Workload, Service, ConfigMap, Secret 등 K8s 리소스 정보.",
    { space_id: z.string().describe("스페이스 ID") },
    async ({ space_id }) => {
      const data = await flaskGet("/api/arch/k8s-view", { space_id });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "get_arch_versions",
    "아키텍처 분석 버전 이력 조회. 과거 분석 결과와 비교 가능.",
    { space_id: z.string().describe("스페이스 ID") },
    async ({ space_id }) => {
      const data = await flaskGet("/api/arch/versions", { space_id });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),
];
