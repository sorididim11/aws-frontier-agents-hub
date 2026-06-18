# DevOps Agent Release Notes

> 이 파일은 DevOps Agent(AWS Agent Space 기반)의 버전별 변경사항을 추적합니다.
> 변경 발견 시 이 파일을 업데이트하면 Expert Agent RAG에 자동 반영됩니다.

## 호환성 매트릭스

| Agent 버전 | 우리 코드 호환 | 필요 조치 | 확인일 |
|-----------|---------------|----------|--------|
| v1.0 (현재) | O | 없음 | 2026-05-22 |

---

## v1.0 — 현재 운영 버전

**확인일:** 2026-05-22

### API 엔드포인트
- `InvokeAgent` — 세션 기반 대화 (executionId = sessionId)
- `GetAgentMemory` / `DeleteAgentMemory` — 메모리 관리
- Agent Space 콘솔에서 스킬(Action Group) 등록/관리

### CLI
- AWS CLI: `aws bedrock-agent-runtime invoke-agent`
- boto3: `bedrock_agent_runtime_client.invoke_agent()`

### 스킬 (Action Groups)
- OpenAPI schema + Lambda 기반
- knowledge_item_id 참조 가능
- 현재 등록 스킬: `.claude/skills/` 참조

### 알려진 제약
- Read-only: EKS kubectl, CloudWatch 조회 가능. 파일 쓰기 불가.
- 코드 생성 가능하나 실행은 앱에서 subprocess로 수행
- boto3 런타임 v7 호환 이슈 → Lambda Layer(boto3 1.42.97) 필요

### 우리 코드 영향 영역
- `services/dashboard/chat_worker.py` — Agent 세션 관리
- `services/dashboard/scenarios/` — 시나리오 정의 (Agent에게 전송)
- `infrastructure/cloudformation/04-devops-agent.yml` — Agent 인프라
- `.claude/skills/*/SKILL.md` — 스킬 정의 파일

---

## 변경 추적 방법

1. AWS 콘솔 > Bedrock > Agent Space에서 버전/설정 변경 확인
2. AWS 공식 릴리즈 노트 확인 (Bedrock Agent 관련)
3. 변경 발견 시 이 파일에 새 버전 섹션 추가
4. `npm run index-docs` 실행하여 RAG 재인덱싱
5. Expert Agent에게 "devops agent 변경사항" 질문하여 반영 확인
