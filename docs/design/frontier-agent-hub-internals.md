# Frontier Agent Hub Internals

> `services/dashboard/overview_app.py` — port 5003
> 마지막 갱신: 2026-05-28

---

## 1. Architecture Overview

### Entry Point & Boot Sequence

1. Load `app_config.py` (config.yaml → _CFG globals)
2. Register 10 Flask blueprints
3. Inject `expert_panel.js` into all HTML responses (after_request hook)
4. `_init_simulator()` — if RUNS_TABLE configured: cluster_manager.init() + init_slack_config() + init_provider()
5. `_start_expert_sidecar()` — spawn Node.js Claude Code sidecar (npm run dev in expert_sidecar/)

### Request Lifecycle Hooks

- `_check_setup()` — local mode: redirect to /setup if config incomplete
- `_log_request()` / `_log_response()` — DEBUG-level request/response logging
- `inject_expert_panel()` — inject expert_panel.js script tag before `</body>`

---

## 2. Blueprint Registry

| Blueprint | File | URL Prefix | Purpose |
|-----------|------|------------|---------|
| setup_bp | routes_setup.py | /setup, /api/setup/ | 초기 설정 위자드 (profile, tables, clusters) |
| space_bp | routes_space.py | / (root), /api/ | 핵심 Space 대시보드, 히스토리, 데이터소스 |
| dag_bp | routes_dag.py | /dag, /api/ | Investigation DAG 시각화 |
| arch_bp | routes_arch.py | /api/arch/ | 아키텍처 분석/버전 관리 |
| scenario_bp | routes_scenario.py | /api/scenario-*, /api/arch/ | 시나리오 생성/검증/저장 |
| settings_bp | routes_settings.py | /settings, /api/settings/ | Security Agent 설정 |
| security_targets_bp | routes_security_targets.py | /api/security/targets | Security Space 생성/연동 |
| security_insights_bp | routes_security_insights.py | /security/insights, /api/security/insights/ | SAST 취약점 + 공격 경로 + 교정 |
| skills_bp | routes_skills.py | /api/skills/ | Skill CRUD (로컬 + 원격 Agent Space) |
| expert_bp | routes_expert.py | /api/expert/, /api/agent-* | Claude Code 사이드카 채팅 + Agent Space 채팅 |

---

## 3. Module Dependency Graph

```
config.yaml
    ↓
app_config.py (_CFG, reload_cfg, session helpers)
    ↓
    ├→ cluster_manager.init()
    │   ├→ account_registry (multi-source: yaml > env > Agent Space)
    │   ├→ topology_provider (kubectl service discovery, 60s refresh)
    │   └→ credential_resolver (profile > STS AssumeRole, 55min cache)
    │
    ├→ ai_provider.py (pluggable: bedrock | agent_space)
    │   ├→ providers/bedrock_direct.py (Bedrock converse + tool loop)
    │   │   ├→ providers/tool_executor.py (whitelist: kubectl/aws/read_file)
    │   │   └→ providers/system_prompts.py (Korean scenario gen prompts)
    │   └→ providers/agent_space.py → chat_worker.py (daemon thread → devops-agent API)
    │
    ├→ execution_context.py (per-scenario credential routing)
    │
    └→ verifier.py (re-exports from verifier_base/checkers/executors/utils)
        ├→ verifier_base.py (SimulationRun — lifecycle + steps)
        ├→ verifier_checkers.py (VERIFIERS dict, error classification)
        ├→ verifier_executors.py (Agent/Script/Python executors)
        └→ verifier_utils.py (AWS/K8s helpers, DDB ops, Slack)
```

---

## 4. Subsystems

### 4.1 Multi-Account Discovery

`account_registry → topology_provider → credential_resolver`

- **AccountRegistry**: merges config.yaml clusters + env files + Agent Space associations
- **TopologyProvider**: kubectl scan per context, maps service_name → (account, context, profile), 60s staleness refresh
- **CredentialResolver**: profile-preferred over STS role; 55-min session cache

### 4.2 AI Provider System

Two backends (`config.yaml ai.provider`):

| Backend | 동작 | 도구 실행 |
|---------|------|----------|
| bedrock | Bedrock converse API + max 20 tool rounds | 로컬 (kubectl_exec, aws_cli_exec, read_file) — whitelist 기반 |
| agent_space | ChatWorker daemon thread → devops-agent API event stream | Agent Space 내부 (DevOps Agent가 실행) |

### 4.3 Simulation Engine

`scenario_runner.py` (Python step-based) + `verifier.py` (lifecycle management)

- **ScenarioRunner**: step decorator pattern, ScenarioContext (kubectl, alarm polling, port-forward)
- **SimulationRun**: step pipeline (preflight → cleanup → trigger → verify → restore)
- **Executors**: AgentExecutor, AgentSSEExecutor, ScriptExecutor, ScriptSSEExecutor, PythonScriptExecutor

### 4.4 Architecture Analysis

`arch_analysis.py` (2800+ lines) — Agent-based topology discovery

- **ArchitectAgent**: Bedrock converse agent with tool use
- **ArchitectureAgentDiscoverer**: single-app Q2 discovery + boundary expansion
- **ArchitectureRecommender**: chaos/security scenario recommendations from graph
- **ScenarioGenerator**: recommendations → executable scenario JSON
- **arch_worker.py**: subprocess for long-running analysis; events via DDB → SSE

### 4.5 Expert Sidecar

`expert_sidecar/` — Node.js Claude Code SDK process

- Flask proxies `/api/expert/chat` → localhost:3100
- SSE streaming for real-time chat
- `EXPERT_CWD` = project root

---

## 5. Key Data Models

### 5.1 Scenario JSON (dockercoins-scenarios/*.json)

```json
{
  "id": "A02-latency",
  "name": "Cascading Latency",
  "category": "single-service",
  "layer": "Application → Performance",
  "purpose": "설명 텍스트",
  "normal_flow": [{"step": "...", "desc": "..."}],
  "fault_flow": [{"step": "...", "desc": "..."}],
  "investigation_goal": "Agent가 무엇을 해야 하는가",
  "expected_root_cause": "예상 근본 원인",
  "trigger": {
    "type": "kubectl | aws | fis | http",
    "command": "실행할 명령"
  },
  "pre_cleanup": {
    "command": "사전 초기화 명령",
    "reset_alarms": ["alarm-name-with-${PROJECT_NAME}"],
    "wait_ok_timeout": 60
  },
  "restore": {
    "command": "복원 명령"
  },
  "verification": {
    "steps": [
      {
        "name": "표시 이름",
        "type": "verification_type",
        "timeout": 300,
        "poll_interval": 15
      }
    ]
  }
}
```

### 5.2 Verification Step Types (VERIFIERS dict)

| type | key fields | purpose |
|------|-----------|---------|
| pod_logs | pod_selector, pattern | grep pod logs for pattern |
| xray_trace | filter, minutes | X-Ray trace search |
| cw_alarm | alarm, expected (ALARM/OK) | CloudWatch alarm state |
| lambda_logs | function_name, pattern | Lambda log pattern match |
| pod_status | selector, expected_status | Pod phase/condition check |
| slack_message | channel, pattern | Slack webhook verification |
| investigation_event | expected_status (IN_PROGRESS/COMPLETED) | DevOps Agent task status |
| fis_experiment | experiment_template_id | FIS experiment completion |
| metric_check | namespace, metric_name, threshold | CloudWatch metric value |
| log_pattern | log_group, pattern | CloudWatch Logs Insights |
| alarm_state | alarm, expected | Same as cw_alarm (alias) |
| api_call | method, url, expected_status | HTTP endpoint check |
| kubectl_check | command, expected_output | kubectl command output match |
| agent_investigation | expected | Agent investigation content check |
| manual | — | Human verification required |

### 5.3 Error Categories

| category | handling |
|----------|---------|
| timeout | mechanical retry (traffic boost, reinject, timeout extension) |
| command_error | send error+stderr to Agent → corrected command → re-run |
| config_error | send config+actual state to Agent → corrected config → re-verify |
| infra_missing | show fix instructions + mark scenario as blocked |
| transient | auto-retry with backoff, escalate after 3 failures |

### 5.4 SimulationRun Step Pipeline

```
[pipeline_preflight] → [pipeline_cleanup?] → [pipeline_trigger] → [verify_step_1..N] → [pipeline_restore?]
```

Step dict structure:
```python
{
    "name": "표시 이름",
    "type": "pipeline_preflight | pipeline_cleanup | pipeline_trigger | pipeline_restore | <verifier_type>",
    "config": {},
    "status": "pending | running | pass | fail | skip",
    "detail": "결과 설명",
    "elapsed": float | None,
    "checked_at": str | None,
    "events": [{"t": timestamp, "msg": "..."}]
}
```

Status lifecycle: `running → verifying → executing → pass | fail | error | timeout | interrupted`

### 5.5 Architecture Models

```python
ServiceGraph:
  nodes: [ServiceNode(name, namespace, kind, labels, ports, service_type, group)]
  edges: [ServiceEdge(source, target, protocol, port, paths, methods, description)]
  namespace: str
  discovered_at: float

AnalysisResult:
  system_name, description, workflows, taxonomy
  graph: ServiceGraph
  compute, managed_services, spof, blast_radius
  external_deps, observability_gaps, tag_gaps
  conversations: {phase_id: [messages]}
  k8s_detail: {service: {replicas, resources, probes}}

Recommendation:
  template_id, name, target: {service, port, namespace}
  priority: "critical" | "high" | "medium" | "low"
  rationale, expected_impact, detection_challenge
```

### 5.6 Multi-Account Models

```python
RegisteredAccount:
  account_id, profile, role_arn, region
  account_type: "primary" | "secondary"
  source: "config" | "env" | "agent_space"
  contexts: [str], clusters: [{name, arn, context}]

ServiceLocation:
  service_name, account_id, context, namespace, cluster_label, replicas

ExecutionContext:
  target_service, account_id, profile, kubectl_context, region, namespace
```

### 5.7 config.yaml Schema

```yaml
project:
  name: "frontier-agent-hub"
server:
  port: 5003
  sidecar_port: 3100
aws:
  region: us-east-1
  account_id: "222222222222"
  profile: member2-acc
kubernetes:
  namespace: dockercoins
clusters:
  primary:
    account_id, context, profile, services: [...]
  secondary:
    account_id, context, profile
dynamodb:
  events_table, runs_table, findings_table
executor:
  default: "classic" | "agent"
ai:
  provider: "bedrock" | "agent_space"
bedrock:
  default_model: opus
  models: {haiku, sonnet, opus}
```

---

## 6. Key Feature Flows

### 6.1 Scenario Execution

```
POST /api/scenario/run
  → Load scenario JSON (DDB or local)
  → Variable resolution: ${PROJECT_NAME}, ${NAMESPACE}, discovery variables
  → SimulationRun(scenario) → ExecutionContext.for_scenario()
  → Pipeline (daemon thread):
    a. Pre-flight: tool + K8s + AWS + target ready
    b. Pre-cleanup (optional): reset alarms, clear prior injection
    c. Trigger: execute command
    d. Verify: poll each VERIFIER until pass/timeout
    e. Restore (optional): undo injection
  → Events → DDB + SSE
  → Final: pass/fail with error_category
```

### 6.2 Architecture Analysis

```
POST /api/arch/analyze
  → Spawn arch_worker.py SUBPROCESS (survives Flask restart)
  → ArchitectureAgentDiscoverer._discover_single_app():
    a. Load arch_questions.json Q2, substitute {app_name}
    b. Send ONE question to Agent (ChatWorker → Agent Space)
    c. Agent discovers: tagged resources + K8s workloads + flows + boundary nodes
    d. Parse → ServiceGraph + AnalysisResult
  → Events to DDB → Flask SSE → frontend
  → Save final AnalysisResult to DDB
```

### 6.3 Scenario Generation

```
POST /api/arch/generate-scenario
  → Load latest AnalysisResult (ServiceGraph + recommendations)
  → ArchitectureRecommender.recommend(graph) → RecommendationResult
  → User selects recommendation
  → ScenarioGenerator.generate(recommendation, graph) → scenario JSON
  → Validate → Save to DDB
```

### 6.4 Expert Chat

```
POST /api/expert/chat
  → Flask proxy → Node.js sidecar (localhost:3100)
  → Claude Code SDK (EXPERT_CWD = project root)
  → SSE stream back → frontend right-slide panel

POST /api/agent-chat (separate)
  → ChatWorker.send(space_id, session_id, prompt)
  → Daemon thread → devops-agent API event stream
  → Parse ChatResponse → return
```

### 6.5 Skill Deployment

```
POST /api/skills/deploy
  → Read local skill file
  → SkillManager: compare with remote Agent Space
  → Create or update knowledge item (agent_types = ["GENERIC", ...])
  → Invalidate cache → return sync_status: "synced"
```

### 6.6 Security Insights

```
GET /api/security/insights/enriched-findings
  → Resolve linked Security Agent Space (DDB security_links)
  → Security Agent API: pentests → jobs → tasks
  → Parse SAST findings
  → Enrich with context (code location, severity, remediation scenarios)
  → Cache per space_id (TTL)
```

### 6.7 Setup Wizard

```
GET /setup → Wizard UI
  a. AWS Profile: list ~/.aws → validate STS
  b. DynamoDB: discover or auto-create tables
  c. EKS: auto-discover clusters per account
  d. Agent Space: validate connectivity
POST /api/setup/save → write config.yaml → reload_cfg()
```

---

## 7. Cross-Cutting Patterns

| Pattern | Implementation |
|---------|---------------|
| Long-running ops | DDB event stream + SSE relay (arch, scenario) |
| Multi-account routing | ExecutionContext resolves per-service via topology_provider |
| AI provider switch | `ai.provider`: bedrock (local tools) or agent_space (delegate) |
| Caching | TTL-based in-memory (5s arch, 60s topology, 55min sessions) |
| Variable resolution | `${PROJECT_NAME}`, `${NAMESPACE}` + discovery (ARN, alarm via AWS API) |
| Error recovery | Category-based: timeout/transient=retry, command/config=Agent, infra=block |
| Hot reload | reload_cfg() propagates to all modules (WARNING: kills analysis threads) |
