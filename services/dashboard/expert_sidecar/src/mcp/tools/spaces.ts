import { tool } from "@anthropic-ai/claude-code";
import { z } from "zod";
import { flaskGet } from "../flask-client.js";

export const spaceTools = [
  tool(
    "list_spaces",
    "전체 Agent Space 목록 조회. 각 스페이스의 이름, ID, 상태, 앱 태그 정보 포함.",
    {},
    async () => {
      const data = await flaskGet("/api/spaces");
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "get_space_info",
    "특정 스페이스의 상세 정보 조회. 메타데이터, 권한, 태그, 클러스터 정보 등.",
    { space_id: z.string().describe("스페이스 ID") },
    async ({ space_id }) => {
      const data = await flaskGet("/api/space-info", { space_id });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "get_tagged_resources",
    "스페이스 내 AWS App 태그가 붙은 리소스 전체 목록 조회. EC2, EKS, RDS, Lambda 등.",
    { space_id: z.string().describe("스페이스 ID") },
    async ({ space_id }) => {
      const data = await flaskGet("/api/tagged-resources-all", { space_id });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),
];
