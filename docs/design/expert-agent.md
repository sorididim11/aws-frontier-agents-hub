# Expert Agent 패널 & AI Provider

## 개요

Expert 패널은 사용자가 Claude Code/Kiro CLI와 대화하며 DevOps Agent의 도구를 활용할 수 있는 인터페이스다.
Node.js 사이드카가 CLI를 감싸고, Flask가 프록시하며, MCP 도구로 frontier-agent-hub 백엔드에 콜백한다.

---

## 1. 아키텍처

```
Browser (expert_panel.js)
    │ SSE stream
    ▼
Flask /api/expert/chat (routes_expert.py)
    │ HTTP proxy
    ▼
Node.js Sidecar (:3100, expert_sidecar/)
    │ Claude Code SDK / Kiro CLI
    ▼
Claude Code 로컬 프로세스
    │ MCP tools
    ▼
Flask backend (/api/agent-chat, /api/topology, etc.)
```

### 설계 결정

| 결정 | 이유 |
|------|------|
| 별도 Node.js 프로세스 | CLI I/O 블로킹 격리, Flask 이벤트 루프 보호 |
| SSE 스트리밍 | 실시간 채팅, 버퍼링 없음 |
| MCP 콜백 | Claude Code가 frontier-agent-hub 도구 사용 가능 |

---

## 2. 사이드카 라이프사이클

### 시작

1. Provider 감지: `which claude` / `which kiro-cli` (+ env var fallback)
2. Port 결정: config.yaml `sidecar_port` OR env `SIDECAR_PORT` OR 3100
3. Flask URL 설정: `FLASK_API_URL` env (MCP 콜백용)
4. RAG embeddings 로드 (optional): `../data/embeddings.json`
5. Hono 서버 시작: `serve({ port: sidecarPort })`

### Provider 우선순위

1. config.yaml `expert.providers.claude.path`
2. env `CLAUDE_CODE_PATH` / `KIRO_CLI_PATH`
3. PATH에서 자동 탐지 (`which`)
4. 기본값: 첫 번째 발견된 provider

---

## 3. 세션 모델

### 계층 구조

```
Browser localStorage
  ├─ expert_session_id (UUID)
  ├─ expert_chat_history [{role, content, timestamp}]
  └─ expert_provider ("claude" | "kiro")

Sidecar
  └─ session_id → Claude SDK resume option

ChatWorker (Agent Space용)
  └─ executionId → Agent API 세션 (서버 측 ~1시간)
```

### 흐름

| 단계 | 첫 채팅 | 이후 채팅 |
|------|---------|-----------|
| Browser | sessionId 없음 | localStorage에서 복원 |
| Sidecar | 새 Claude Code 세션 | `resume: sessionId` |
| Agent Space | `create_chat()` → executionId | 기존 executionId 재사용 |

---

## 4. MCP 도구 (8개 카테고리)

사이드카가 Claude Code에 제공하는 MCP 서버:

| 카테고리 | 도구 예시 | Flask 콜백 |
|---------|-----------|------------|
| agent_chat | send_agent_message, start_investigation | /api/agent-chat |
| topology | analyze, expand, validate | /api/topology |
| datasource | list, add, validate | /api/spaces/datasources |
| scenarios | list, create, run, validate | /api/scenarios |
| investigation | analyze logs/metrics/traces | /api/investigation |
| security | scan risks, check policy | /api/security |
| skills | list, invoke | /api/skills |
| knowledge | query knowledge base | /api/knowledge |

### Flask-MCP 브릿지

```typescript
// flask-client.ts
const FLASK_URL = process.env.FLASK_API_URL || "http://localhost:5003"
export async function flaskPost(path, body) { ... }
```

---

## 5. AI Provider 시스템

### 인터페이스

```python
class AIProvider(ABC):
    send_raw(space_id, session_id, prompt) → {"ok", "reply", "session_id"}
    generate(prompt, model_id="") → {"ok", "reply"}
    generate_with_tools(prompt, tools, tool_executor) → {"ok", "reply", "tool_calls"}
```

### Provider 선택 (`config.yaml → ai.provider`)

| Provider | 클래스 | 용도 |
|----------|--------|------|
| `agent_space` (기본) | AgentSpaceProvider | Agent Space 멀티턴 대화 |
| `bedrock` | BedrockDirectProvider | 로컬 도구 실행 + Bedrock API |
| `strands` | StrandsProvider | Strands SDK Agent |

### 사용 분기

| 시나리오 | Provider | 이유 |
|---------|----------|------|
| 시나리오 실행/교정 | Bedrock | 로컬 도구 즉시 피드백 |
| Agent Space 채팅 | Agent Space | 멀티 계정/서비스 오케스트레이션 |
| Expert 패널 | Claude Code (사이드카) | 로컬 CLI + MCP 도구 |

---

## 6. ChatWorker (Agent Space 통신)

### 아키텍처

```
Flask 메인 스레드 → ChatRequest 큐잉
    ↓
ChatWorker Daemon Thread (단일, 순차 처리)
    ↓
Agent API send_message() + 이벤트 스트림 파싱
    ↓
ChatRequest.done.set() → 메인 스레드 unblock
```

### 핵심 기능

| 기능 | 설명 |
|------|------|
| Multi-account | DDB에서 space owner 조회 → 계정별 boto3 client 캐시 |
| Localhost 치환 | 프롬프트의 localhost→LOCAL_ENDPOINT, 응답에서 복원 |
| 세션 관리 | create_chat() → executionId 발급, 재사용 |
| Retry | 403 → 새 세션 생성 후 재시도 (최대 3회, 5초 간격) |
| 응답 파싱 | contentBlockStart/Delta → ChatResponse blocks |

### 제약

- **단일 스레드**: boto3 connection pool 충돌 방지
- **순차 처리**: 동일 세션 concurrent call 방지
- **Daemon**: Flask 앱 생명주기에 종속

---

## 7. Bedrock Provider 상세

### 특징

- **Stateful**: 세션별 메시지 히스토리 (in-memory `_sessions` dict)
- **Tool-use loop**: `stopReason == "tool_use"` → 로컬 실행 → 결과 전송 → 반복
- **System prompt 선택**: 프롬프트 텍스트 heuristic (code_fix, scenario_fix, improvements)
- **Max rounds**: 20 (tool-use 반복 상한)

### 한계

- 재시작 시 세션 소실 (in-memory)
- 수평 확장 불가 (stateful)

---

## 8. 주요 함수 참조

| 함수/클래스 | 파일 | 역할 |
|------------|------|------|
| expert_chat (SSE) | routes_expert.py | Flask→사이드카 프록시 |
| agent_chat | routes_expert.py | ChatWorker 래퍼 |
| ChatWorker | chat_worker.py | Agent Space 데몬 스레드 |
| AIProvider | ai_provider.py | 추상 인터페이스 |
| BedrockDirectProvider | providers/bedrock_direct.py | Bedrock + 로컬 도구 |
| AgentSpaceProvider | providers/agent_space.py | ChatWorker 래퍼 |
| index.ts | expert_sidecar/src/ | 사이드카 서버 |
| flask-client.ts | expert_sidecar/src/mcp/ | MCP→Flask 브릿지 |

---

## 9. 설계 원칙

1. **격리**: CLI 프로세스를 별도 사이드카로 격리 → Flask 안정성 보장
2. **스트리밍 우선**: SSE로 실시간 응답 (polling 없음)
3. **Pluggable provider**: 새 AI 백엔드는 AIProvider 인터페이스만 구현
4. **MCP 순환**: Claude Code → MCP → Flask → Agent Space → 응답 → Claude Code
5. **Localhost 보호**: Agent Space에 localhost URL 유출 방지
6. **단일 스레드 안전**: ChatWorker 순차 처리로 race condition 제거
