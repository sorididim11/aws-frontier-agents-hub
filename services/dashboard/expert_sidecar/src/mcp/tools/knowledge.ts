import { tool } from "@anthropic-ai/claude-code";
import { z } from "zod";
import { ragSearch, isLoaded } from "../../rag/index.js";

export const knowledgeTools = [
  tool(
    "search_knowledge",
    "프로젝트 문서, 스킬 정의, 시나리오 템플릿, 장애 모드 등을 의미(semantic) 검색. DevOps Agent/Security Agent 관련 지식 조회에 사용.",
    {
      query: z.string().describe("검색 질의 (한국어 또는 영어)"),
      top_k: z.number().optional().describe("반환할 최대 결과 수 (기본 5)"),
    },
    async ({ query, top_k }) => {
      if (!isLoaded()) {
        return {
          content: [
            {
              type: "text" as const,
              text: "RAG 인덱스가 준비되지 않았습니다. 'npm run index-docs'를 먼저 실행해야 합니다.",
            },
          ],
        };
      }
      const results = await ragSearch(query, top_k || 5);
      const text = results
        .map(
          (r) =>
            `## [${r.source}] ${r.section} (score: ${r.score.toFixed(3)})\n${r.content}`
        )
        .join("\n\n---\n\n");
      return { content: [{ type: "text" as const, text }] };
    }
  ),
];
