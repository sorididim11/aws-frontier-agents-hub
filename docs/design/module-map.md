# Module Map — Frontier Agent Hub

## 등록된 Blueprint (overview_app.py 기준, 9개)

| Blueprint | File | Purpose |
|-----------|------|---------|
| space_bp | routes_space.py | Space 대시보드, 히스토리, 데이터소스 CRUD |
| dag_bp | routes_dag.py | Investigation DAG 시각화 |
| arch_bp | routes_arch.py | 아키텍처 분석/버전 관리 |
| scenario_bp | routes_scenario.py | 시나리오 생성/검증/저장/실행 |
| settings_bp | routes_settings.py | Security Agent 설정, Pentest 관리 |
| security_targets_bp | routes_security_targets.py | Security Space 생성/연동/매칭 |
| security_insights_bp | routes_security_insights.py | SAST 취약점 + Chain Attack + Fix Priority |
| cfn_import_bp | routes_space_cfn_import.py | CFn Import/Disconnect/Status |
| skills_bp | routes_skills.py | Skill CRUD (Asset API) + AWS 카탈로그 + 추천 |

**미등록 (import되지 않으나 존재하는 모듈):**
- `routes_space_registry.py` (registry_bp) — Space Discover/Register API
- `routes_setup.py` (setup_bp) — 초기 설정 위자드
- `routes_expert.py` (expert_bp) — Claude Code 사이드카 + Agent Space 채팅

---

## 모듈 의존성 그래프

```
config.yaml
    ↓
app_config.py (_CFG, reload_cfg)
    ↓
    ├→ cluster_manager.init()
    │   ├→ account_registry (yaml > env > Agent Space)
    │   ├→ topology_provider (kubectl discovery, 60s refresh)
    │   └→ credential_resolver (profile > STS, 55min cache)
    │
    ├→ ai_provider.py (bedrock | agent_space)
    │   ├→ providers/bedrock_direct.py (converse + tool loop)
    │   └→ providers/agent_space.py → chat_worker.py (daemon → devops-agent API)
    │
    ├→ execution_context.py (per-scenario credential routing)
    │
    ├→ verifier.py → verifier_base/checkers/executors/utils
    │
    ├→ multi_agent_engine.py (Generator + Verifier + Evaluator 3-Agent)
    │
    └→ security_enrichment.py + security_findings_store.py
```

---

## 핵심 서브시스템 요약

### 1. Space 관리 (`routes_space.py`, `routes_space_cfn_import.py`, `routes_space_registry.py`)
- Wizard(CFn) vs Discover(Direct API) 두 경로
- CFn Import/Disconnect로 managed↔unmanaged 전환
- Drift 감지: settings_version vs last_deployed_version
- **doc**: `docs/space-lifecycle.md`

### 2. Security Agent 연동 (`routes_security_targets.py`, `routes_security_insights.py`)
- DevOps Space ↔ Security Space (1:N)
- 파이프라인: pentest → findings → chain attack 분석 → fix priority → auto remediation(PR)
- 캐시 3계층: Memory(5min) → Disk(.skill-cache/) → DDB
- **doc**: `docs/security.md`, `docs/security-agent-test-plan.md`

### 3. 시나리오 실행 엔진 (`routes_scenario.py`, `verifier*.py`, `engine*.py`, `failure_modes.py`)
- FM-01~FM-22 장애 모드 템플릿 → 아키텍처 기반 추천 → 시나리오 생성 → 실행 → 자동 교정
- 3 executor: Classic, Script, Engine(phased)
- Pipeline: preflight → cleanup → trigger → verify → restore
- 3-Agent 아키텍처: Generator(시나리오 생성) + Verifier(검증) + Evaluator(평가)
- **doc**: `docs/scenario-engine.md`, `docs/test-scenarios.md`, `docs/test-results.md`

### 4. 아키텍처 분석 (`arch_analysis.py`, `routes_arch.py`)
- ArchitectureAgentDiscoverer: single-app Q2 + boundary expansion
- ArchitectureRecommender → ScenarioGenerator
- arch_worker.py subprocess, DDB event stream → SSE
- **doc**: `docs/arch-analysis-context.md`

### 5. Expert Sidecar (`expert_sidecar/`, `routes_expert.py`)
- Node.js Claude Code SDK (localhost:3100)
- Flask proxy + SSE streaming
- MCP 콜백으로 frontier-agent-hub 도구 활용
- **doc**: `docs/expert-agent.md`

### 6. Skill 관리 (`skill_manager.py`, `catalog_manager.py`, `routes_skills.py`)
- API-first: 모든 CRUD는 Asset API 단일 경로 (채팅 fallback 제거됨)
- 사용자 정의 스킬: UI에서 content 직접 작성/수정 → create_asset/update_asset
- 개발자 워크플로우: `skills/` 디렉토리 편집 → deploy() → ZIP → Asset API
- 모듈형 빌드: `build.sh` 존재 시 `src/*.md` → SKILL.md 자동 조립
- AWS Skill Catalog: GitHub 225개 스킬, git sparse-clone 수집, 수동 refresh only
- 토폴로지 기반 추천: arch Q2 nodes[].kind → KIND_TO_CATALOG 매핑
- 자동 배포: DEFAULT_SKILLS (`arch-discover`, `k8s-detail`)
- **doc**: `docs/skills.md`

### 7. Investigation DAG (`routes_dag.py`)
- Agent 조사 과정을 가설 기반 그래프로 재구성/시각화
- **doc**: `docs/investigation-dag.md`, `docs/investigation-flow-patterns.md`

### 8. Multi-Account (`account_registry.py`, `topology_provider.py`, `credential_resolver.py`)
- AccountRegistry: config.yaml + env + Agent Space associations 병합
- TopologyProvider: kubectl scan, service→(account, context, profile), 60s refresh
- CredentialResolver: profile 우선, STS fallback, 55min cache
- **doc**: `docs/account-usage.md`, `docs/infra-resolution.md`

---

## 문서 전체 인덱스

| 문서 | 대상 | 내용 |
|------|------|------|
| `docs/design-decisions.md` | 전체 | 설계 결정(ADR 경량판) — 왜 이렇게 만들었는지 |
| `docs/frontier-agent-hub-internals.md` | 전체 | 모듈 의존성, 데이터 모델, Feature Flow 상세 |
| `docs/space-lifecycle.md` | Space 관리 | Space CRUD, CFn, Drift, DDB 스키마 |
| `docs/security.md` | Security | pentest→findings→chain attack→remediation |
| `docs/security-agent-test-plan.md` | Security | Security Agent 테스트 계획 |
| `docs/scenario-engine.md` | 시나리오 | 실행 3경로, 자동 교정, FM 템플릿 |
| `docs/test-scenarios.md` | 시나리오 | 시나리오 스펙 정의서 |
| `docs/test-results.md` | 시나리오 | E2E 테스트 결과 기록 |
| `docs/expert-agent.md` | Expert | 사이드카 아키텍처, MCP 콜백 |
| `docs/skills.md` | Skill | API-first CRUD, AWS 카탈로그, 토폴로지 추천, 개발자 워크플로우 |
| `docs/arch-analysis-context.md` | 아키텍처 분석 | Agent 기반 토폴로지 발견 설계 |
| `docs/investigation-dag.md` | DAG | 가설 기반 조사 그래프 |
| `docs/investigation-flow-patterns.md` | DAG | 조사 흐름 패턴 분석 |
| `docs/account-usage.md` | Multi-Account | Account-Profile 매핑 |
| `docs/infra-resolution.md` | Multi-Account | 4-Layer resolution 체인 |
| `docs/gitlab-private-connection-setup.md` | 연동 | GitLab CE → Agent Space 연결 절차 |
| `docs/splunk-cloud-integration-guide.md` | 연동 | Splunk Cloud → Agent Space 연결 |
| `docs/TODO.md` | 전체 | 모듈별 작업 추적 (claim 규칙) |

---

## 주요 데이터 흐름

```
[시나리오 실행]  POST /api/scenario/run → SimulationRun → pipeline thread → DDB+SSE
[아키텍처 분석]  POST /api/arch/analyze → arch_worker subprocess → Agent Space → DDB+SSE
[Security 스캔]  POST /api/settings/security/pentest/start → Security Agent → findings → enrichment
[Expert 채팅]   POST /api/expert/chat → Flask proxy → Node.js sidecar → Claude Code SDK
[Agent 채팅]    POST /api/agent-chat → ChatWorker daemon → devops-agent API
[Skill 배포]    POST /api/skills/create → SkillManager → Asset API (create_asset/update_asset)
[Space 생성]    POST /api/spaces/deploy-cfn → CFn stack → outputs → DDB metadata
[Space 발견]    GET /api/spaces/discover → Organizations scan → register → DDB
```

---

## Cross-Cutting 패턴

| Pattern | Implementation |
|---------|---------------|
| Long-running ops | DDB event stream + SSE relay |
| Multi-account | ExecutionContext per-service via topology_provider |
| AI provider switch | bedrock (local tools) or agent_space (delegate) |
| Variable resolution | `${PROJECT_NAME}`, `${NAMESPACE}` + discovery |
| Error recovery | timeout/transient=retry, command/config=Agent, infra=block |
| 캐시 전략 | Memory → Disk → DDB (TTL별 계층화) |
