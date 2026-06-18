---
paths:
  - services/dashboard/ai_provider.py
  - services/dashboard/providers/
  - services/dashboard/chat_worker.py
---

# AI Provider 규칙

- bedrock (local tools) 또는 agent_space (delegate) — 두 경로만 존재
- Bedrock tool_use: `inputSchema`에 `required` 필드 반드시 포함
- AI 질문 구성: 출력 형식 먼저 정의 → ONE question에 임베드 (다수 질문 = 품질 저하)
- Worker는 daemon thread 패턴 — `subprocess.Popen` + DDB polling 절대 금지
- chat_worker는 `get_or_create_session()` — executionId = permanent chat ID
