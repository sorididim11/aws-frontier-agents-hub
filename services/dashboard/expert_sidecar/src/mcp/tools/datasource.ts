import { tool } from "@anthropic-ai/claude-code";
import { z } from "zod";
import { flaskGet, flaskPost } from "../flask-client.js";

export const datasourceTools = [
  tool(
    "list_datasources",
    "스페이스에 연결된 데이터소스(GitLab, Splunk, GitHub) 목록과 상태 조회.",
    { account_id: z.string().optional().describe("계정 ID (미지정시 기본 계정)") },
    async ({ account_id }) => {
      const params: Record<string, string> = {};
      if (account_id) params.account_id = account_id;
      const data = await flaskGet("/api/integrations", params);
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "register_datasource",
    "새 데이터소스 등록 (GitLab 또는 Splunk). provider, host_url, token 등 필요.",
    {
      provider: z.enum(["gitlab", "mcpserversplunk"]).describe("서비스 유형"),
      host_url: z.string().optional().describe("GitLab 서버 URL (gitlab인 경우)"),
      token: z.string().optional().describe("Personal Access Token (gitlab인 경우)"),
      token_type: z.string().optional().describe("토큰 유형: personal|group (기본 personal)"),
      name: z.string().optional().describe("서비스 이름 (splunk인 경우)"),
      endpoint: z.string().optional().describe("Splunk 엔드포인트 URL (splunk인 경우)"),
      auth_type: z.string().optional().describe("인증 방식: bearer_token|api_key|oauth_client"),
      token_value: z.string().optional().describe("Bearer token 값 (splunk bearer_token인 경우)"),
      private_connection_name: z.string().optional().describe("Private Connection 이름 (gitlab인 경우)"),
    },
    async (params) => {
      const data = await flaskPost("/api/integrations/register", params);
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),

  tool(
    "verify_datasource_connection",
    "데이터소스 연결 검증. GitLab의 경우 프로젝트 목록 조회로 확인.",
    { service_id: z.string().describe("서비스 ID") },
    async ({ service_id }) => {
      const data = await flaskGet("/api/integrations/gitlab-repos", { service_id });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    }
  ),
];
