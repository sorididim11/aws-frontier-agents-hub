# 시나리오 실행 엔진

## 개요

시나리오 시스템은 장애 시뮬레이션을 **생성 → 검증 → 자동 개선** 루프로 실행한다.

**현행 (v2 — Simulation Engine):** Strands SDK 네이티브 2-Agent 루프. FM 템플릿 + 대상 선택 → 단일 "생성 & 실행" 흐름.
**레거시 (v1):** Classic/Script/Engine 3경로 + 별도 교정 루프 (하단 참조).

---

## Simulation Engine v2 (simulation_engine/)

### 아키텍처

```
┌──────────── Single Loop (max 3 rounds) ────────────────┐
│                                                         │
│  Generator Agent (Opus)                                 │
│  ├─ kubectl_query, aws_query (환경 탐색)                 │
│  ├─ submit_scenario → L1-L3 검증 (Strands 자동 재시도)   │
│  └─ Artifact (시나리오 JSON) 확정                        │
│                    │                                    │
│                    ▼                                    │
│  Verifier Agent (Sonnet)                                │
│  ├─ execute_command (trigger 실행, whitelist)            │
│  ├─ kubectl_query, aws_query (상태 관찰)                 │
│  ├─ check_command (패턴 매칭)                            │
│  ├─ wait_seconds (효과 전파 대기)                        │
│  └─ Verdict (pass/fail + 실행 증거)                     │
│                    │                                    │
│  pass → 저장 & 완료                                     │
│  fail → Generator.improve(artifact, verdict) → 다음 R   │
└─────────────────────────────────────────────────────────┘
```

### 모듈 구조

| 파일 | 역할 |
|------|------|
| `__init__.py` | Public API: `run_simulation()`, `get_run_status()`, `cancel_run()` |
| `contracts.py` | SimulationRequest, Artifact, Verdict, RunResult, etc. |
| `orchestrator.py` | 루프 제어, 상태 관리, cleanup, 이벤트 발행 |
| `generator.py` | Generator Agent (Opus) — create() + improve() |
| `verifier.py` | Verifier Agent (Sonnet) — verify() → Verdict |
| `tools.py` | 도구 팩토리: make_generator_tools(), make_verifier_tools() |
| `prompts.py` | Generator/Verifier 시스템 프롬프트 |
| `escalation.py` | 전략 전환 판단 (2x same fail → switch, 3x → give_up) |
| `events.py` | SSERelay: thread-safe queue → SSE stream |
| `persistence.py` | DDB 저장 + local fallback |

### API 엔드포인트

| Method | Path | 역할 |
|--------|------|------|
| POST | `/api/simulation/run` | 시뮬레이션 시작, run_id 반환 |
| GET | `/api/simulation/<run_id>/stream` | SSE EventSource |
| GET | `/api/simulation/<run_id>/status` | 상태 조회 (폴링) |
| POST | `/api/simulation/<run_id>/cancel` | 실행 취소 |
| GET | `/api/simulation/active` | 활성 실행 목록 |

### SSE 이벤트

| event_type | data 주요 필드 |
|------------|---------------|
| `round_start` | round, max_rounds |
| `phase_change` | phase (generate/verify/improve) |
| `agent_action` | agent, tool, input_summary |
| `validation` | passed, layer, errors[] |
| `artifact` | scenario_id, scenario_name |
| `verdict` | passed, failure_reason, fix_hint |
| `complete` | result (pass/fail), rounds, final_scenario |
| `error_event` | message |

### 검증 계층 (Progressive Validation)

| Layer | 검증자 | 시점 |
|-------|--------|------|
| L1 Structural | ScenarioStructuralValidator | submit_scenario 내 |
| L2 Semantic | ResourceExistenceValidator, CloudWatchDimensionValidator | submit_scenario 내 |
| L3 Feasibility | TimeoutFeasibilityValidator | submit_scenario 내 |
| L4 Execution | Verifier Agent (실제 클러스터) | Verifier 호출 시 |

### 에스컬레이션 규칙

| 조건 | 액션 |
|------|------|
| 동일 failure_reason 2회 | SWITCH_APPROACH (constraints에 실패 방식 추가) |
| 동일 failure_reason 3회 | GIVE_UP |
| INFRA/CLUSTER 에러 코드 | 즉시 GIVE_UP |

### 안전장치

- execute_command whitelist: `kubectl scale`, `delete pod`, `set resources`, `apply -f`, `rollout restart`, `patch`
- CleanupRegistry: trigger마다 자동 cleanup 등록
- Orchestrator finally: 어떤 예외든 restore + cleanup drain
- Max rounds: 3 (무한 루프 방지)

### UI

- `static/js/simulation_panel.js` + `static/css/simulation.css`
- 시나리오 탭 → 실행 모드 "Simulation v2" 선택 → FM 템플릿 클릭
- SSE EventSource로 실시간 Round 카드 렌더링
- Round 카드: Generator actions → Verifier actions → Verdict 표시

---

## Legacy (v1)

> 아래 내용은 기존 3-경로 실행 엔진(Classic/Script/Engine)에 대한 문서.
> `executor_type`이 없거나 "simulation_v2"가 아닌 시나리오에만 적용.

---

### v1 전체 흐름

시나리오 시스템은 장애 시뮬레이션을 **템플릿 정의 → 추천 → 생성 → 실행 → 교정 → 영구 저장**하는 파이프라인이다.
3가지 실행 경로(Classic, Script, Engine)를 `executor_type`으로 선택하며, 실패 시 자동 교정을 시도하고 교정 결과를 영구 저장한다.

---

## 1. 전체 흐름 (End-to-End Pipeline)

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1. 장애 모드 템플릿 (failure_modes.py)                                │
│    FM-01~FM-22: 플랫폼 독립 장애 정의 + observation_signals           │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 2. 아키텍처 기반 추천 (ArchitectureRecommender)                       │
│    토폴로지 그래프 + FM 목록 → Bedrock → 5-8개 추천 시나리오          │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 3. 시나리오 생성 (ScenarioGenerator)                                  │
│    추천 + 아키텍처 + 알람 + FIS + exemplar → Bedrock → 실행용 JSON    │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 4. 시나리오 실행 (PhasedExecutor)                                     │
│    Prepare → Execute (trigger + verify) → Teardown                  │
└───────────────┬──────────────────────────────┬──────────────────────┘
                │ 실패 시                        │ 성공 시
                ▼                               ▼
┌───────────────────────────┐    ┌─────────────────────────────────┐
│ 5. 자동 교정 (Agent)       │    │ 6. 결과 저장 (DynamoDB)          │
│    Agent 프로빙 → 교정 JSON │    │    실행 결과 + 교정 시나리오     │
│    → 재실행                 │    │    → 영구 반영                   │
└───────────────┬───────────┘    └─────────────────────────────────┘
                │ pass 시
                └───────────────→ (6으로)
```

---

## 2. 장애 모드 템플릿 (`failure_modes.py`)

FM-01~FM-22: 플랫폼 독립 장애 템플릿 (EKS/ECS/EC2/Lambda 공통)

### 구조

```
FM-XX:
  id: "FM-01"
  name: "Network Isolation"
  layer: infrastructure | application | data | observability
  description: 장애 시나리오 설명
  trigger_mode: reactive | proactive | either
  trigger_mechanism: [장애 주입 AWS CLI/FIS 명령]
  observation_signals:
    trigger_active: [장애 주입 확인 신호]    ← "실제로 주입됐는가?"
    effect_observed: [장애 효과 관측 신호]    ← "영향이 나타났는가?"
    reaction_confirmed: [대응 확인 신호]     ← "Agent가 조사했는가?"
  requires: 사전 필요 정보
  applicable_when: 적용 조건
  investigation_prompt: Agent 조사 질문 템플릿
  restore_mechanism: [복원 방법]
```

### observation_signals의 effect_type 분류

| 타입 | 의미 | 검증 방법 | 신뢰도 |
|------|------|-----------|--------|
| `infra_state` | 인프라 상태 직접 확인 | kubectl_check, pod_status | high (보장됨) |
| `metric_observed` | CloudWatch 메트릭 반응 | alarm_state, metric_check | medium (인프라 필요) |
| `app_dependent` | 앱 에러 핸들링 의존 | kubectl_check (fallback) | low (보장 불가) |

### 역할

- 시나리오를 직접 생성하지 않음 — **생성 규칙을 정의**
- `observation_signals`가 핵심: verification steps의 **설계 근거**로 사용됨
- signal 하나 = verification step 하나 (1:1 매핑 원칙)

---

## 3. 아키텍처 기반 추천 (`arch_analysis.py:ArchitectureRecommender`)

### 입력

| 데이터 | 출처 |
|--------|------|
| 서비스 토폴로지 그래프 | 아키텍처 분석 결과 (DDB) |
| FM 목록 (요약) | `FAILURE_MODES` (id, name, layer, applicable_when만) |

### 프로세스

```
ServiceGraph → _build_architecture_summary()
  → 서비스 목록 + 통신 경로 + 메타데이터
  → RECOMMEND_PROMPT.format(architecture_json, templates_json)
  → Bedrock (Claude Opus)
  → JSON: {recommendations: [...], architecture_analysis: {...}}
```

### 출력 — Recommendation 구조

```json
{
  "failure_mode_id": "FM-03",
  "name": "Redis 의존성 블랙홀 시뮬레이션",
  "target": {"service": "worker", "resource": "redis"},
  "priority": "high",
  "trigger_mode": "reactive",
  "rationale": "Worker가 Redis에 100% 의존...",
  "expected_impact": "해시 처리 완전 중단",
  "detection_challenge": "커넥션 풀 재사용으로 간헐적 에러",
  "investigation_prompt": "worker 서비스의 외부 의존성 상태를 점검..."
}
```

---

## 4. 시나리오 생성 (`arch_analysis.py:ScenarioGenerator`)

### 입력 조립

| 파라미터 | 출처 | 역할 |
|----------|------|------|
| `recommendation` | 추천 단계 결과 | 대상 서비스, 장애 유형, 목적 |
| `architecture_json` | ServiceGraph → _build_arch_summary() | 실제 서비스/포트/통신 |
| `alarms_json` | CloudWatch describe_alarms | 기존 알람 (verification용) |
| `fis_templates_json` | FIS list-experiment-templates | 사용 가능한 FIS 실험 |
| `step_types_json` | `VERIFICATION_STEP_TYPES` | 검증 step 스키마 |
| `exemplars_json` | DDB 기존 시나리오 (few-shot) | 출력 형식 가이드 |
| `template` (FM) | `_find_template(fm_id)` | observation_signals |

### 프롬프트 구성 (`prompts/generate_scenario.md`)

```
역할: 카오스 엔지니어링 전문가
입력: 추천 정보 + 아키텍처 + 알람 + FIS + step 타입 + exemplar
규칙:
  - 환경 변수 플레이스홀더 사용 (${PROJECT_NAME}, ${AWS_REGION} 등)
  - trigger: aws_cli 또는 fis만 (앱 endpoint 접근 불가)
  - restore 명령 필수
  - 한국어 텍스트
  - trigger.command: 단일 문자열 (&& 연결)
  - FIS 템플릿 ID 직접 사용 (동적 검색 금지)
출력: 실행 가능한 시나리오 JSON
```

### 생성 → 후처리

```python
ScenarioGenerator.generate():
  1. prompt 조립 (GENERATE_SCENARIO_PROMPT.format(...))
  2. Bedrock invoke_model (Claude Opus, max_tokens=16384)
  3. JSON 파싱 (_extract_recommendation_json)
  4. 후처리:
     - evaluation_rubric 자동 생성 (없으면)
     - 미정의 변수 검출 → variables 필드에 TODO 추가
  5. 반환: 실행 가능 시나리오 JSON
```

### 시나리오 JSON 최종 구조

```json
{
  "id": "G01-redis-blackhole",
  "source": "ai-generated",
  "failure_mode_id": "FM-03",
  "trigger_mode": "reactive",
  "name": "Redis 의존성 블랙홀",
  "category": "infrastructure",
  "layer": "network",
  "purpose": "Worker-Redis 통신 차단 시 Agent 진단 능력 검증",
  "architecture": {"components": [...], "edges": [...], "fault_path": [...]},
  "normal_flow": [{"step": "1. worker → redis", "desc": "PING/GET"}],
  "fault_flow": [{"step": "1. worker → redis", "desc": "Connection refused"}],
  "variables": {"HOSTED_ZONE_ID": {"discovery": "aws route53..."}},
  "trigger": {"type": "kubectl", "command": "kubectl scale deploy/redis --replicas=0 -n ns"},
  "pre_cleanup": {"command": "kubectl scale deploy/redis --replicas=1 -n ns", "reset_alarms": []},
  "restore": {"command": "kubectl scale deploy/redis --replicas=1 -n ns"},
  "verification": {
    "steps": [
      {"name": "Redis 중단 확인", "type": "pod_status", "phase": "trigger_active", ...},
      {"name": "연결 거부 확인", "type": "kubectl_check", "phase": "effect_observed", ...},
      {"name": "조사 시작", "type": "investigation_event", "phase": "reaction_confirmed", ...},
      {"name": "조사 완료", "type": "investigation_event", "phase": "reaction_confirmed", ...}
    ]
  },
  "evaluation_rubric": {"criteria": [...]},
  "infrastructure_gaps": [...]
}
```

---

## 5. 실행 경로 (executor_type 라우팅)

### 결정 로직 (`verifier.py:_resolve_executor_type`)

1. `scenario["executor"]` 필드 확인
2. Fallback: `config.yaml → executor.default`
3. 최종 기본값: `"classic"`

### Path A — Classic (SimulationRun)

- 기본 검증 루프
- Step 타입: manual, alarm_state, kubectl_check 등
- Cross-step inference: 알람 실패 + 조사 성공 → 소급 알람 통과

### Path B — Script (ScriptExecutor / PythonScriptExecutor)

- Bash/Python 스크립트 기반
- stdout 파싱 (CHECKPOINT, EVENT, RESULT 라인)
- Checkpoint 기반 resume 지원

### Path C — Engine (PhasedExecutor)

- 3-Phase 모듈 구조 (아래 상세)
- 실행 전 변수 resolution + 리소스 검증
- 실패 시 Bedrock 기반 전체 Phase 교정
- Cleanup registry로 리소스 정리 보장

---

## 6. Phased Execution Engine

### 3-Phase 라이프사이클

| Phase | Step | 역할 |
|-------|------|------|
| **PREPARE** | pipeline_preflight | 변수 resolution + 리소스 존재 검증 |
| **EXECUTE** | pipeline_cleanup | 사전 정리 (알람 리셋, 잔여 pod 삭제) |
| | pipeline_trigger | 트리거 실행 + cleanup registry에 생성 리소스 등록 |
| | pipeline_effect_confirm | 적응형: effect_check OR settle_delay OR probe |
| | verify steps | verification.steps[] 실행 (phase 순서 guard) |
| **TEARDOWN** | pipeline_restore | restore 명령 (시나리오 변경 원복) |
| | cleanup registry | 등록된 리소스 보장 정리 |

### 실행 흐름 상세

```
_run_pipeline()
  │
  ├─ _phase_prepare()
  │    ├─ 변수 resolution (EngineResolver)
  │    ├─ _pre_flight_check(): K8s 접근, deploy 존재, 알람 존재
  │    ├─ 성공 → 계속
  │    └─ 실패 → _correct_preflight() → [재검증]
  │
  ├─ _phase_execute()
  │    ├─ _execute_cleanup(): pre_cleanup 명령 + 알람 리셋
  │    ├─ _execute_trigger(): trigger.command 실행
  │    │    ├─ 성공 → 계속
  │    │    └─ 실패 → _correct_trigger() → [재실행]
  │    ├─ _confirm_effect(): effect_observed step 사전 확인
  │    └─ _execute_verify_steps(): verification.steps[] 순차 실행
  │         ├─ AdaptiveStepRunner.run() — 상태 수렴 기반 종료
  │         ├─ 성공 → 다음 step
  │         └─ 실패 → _bedrock_correct_step() → [교정 후 재실행]
  │
  ├─ _phase_teardown()
  │    ├─ restore 명령 실행
  │    └─ cleanup_registry.drain()
  │
  ├─ (result == fail) → _attempt_self_correction() (전체 시나리오 레벨)
  │
  └─ finally:
       ├─ _persist_corrections() — pass 시 교정된 시나리오 DDB 영구 저장
       └─ save() — 실행 결과 저장
```

### Variable Resolution (`engine_resolver.py`)

- **Eager resolution**: 실행 전 fail-fast
- `${PROJECT_NAME}`, `${NAMESPACE}` + discovery 변수 (ARN, alarm 등)
- Tiered validation:
  - Primary (blocks): 없으면 실행 중단
  - Secondary (warns): 없어도 경고만

### 리소스 검증

| 리소스 | 검증 방법 |
|--------|-----------|
| CloudWatch Alarm | describe_alarms |
| Deployment | kubectl get deploy |
| Lambda | get_function |
| Log Group | describe_log_groups |

---

## 7. 자동 교정 루프 (Correction Loop)

### 핵심 원리

```
생성 → 실행 → 실패 → 교정(=재생성) → 재실행 → pass → 시나리오 영구 저장
                       ↑                             ↓
                       └── 또 실패하면 ──────────────┘ (1회 제한)
```

- Agent는 **생성자이자 교정자** — 환경을 프로빙하고, 교정된 설정을 반환
- 교정 = 재생성: 같은 Agent가 실제 환경 기반으로 더 정확한 시나리오 조각 생성
- pass 시 교정된 시나리오가 영구 저장 → 다음 실행부터는 교정 없이 바로 pass

### 3가지 교정 Phase

| Phase | 메서드 | 대상 | Agent에게 주는 정보 |
|-------|--------|------|-------------------|
| Preflight | `_correct_preflight()` | 인프라 부재 | 실패 checks + 시나리오 목적 |
| Trigger | `_correct_trigger()` | 명령 실패 | 명령 + 에러 출력 + 환경 |
| Verify | `_bedrock_correct_step()` | step 실패 | step config + 실패 detail + 타이밍 |

### Preflight 교정 (`_correct_preflight`)

```
실패한 checks (예: "deploy/redis 없음")
  → Agent 호출 (kubectl/aws 프로빙 tool-use)
  → Agent 반환: {"setup_commands": ["kubectl apply...", "kubectl wait..."], "reasoning": "..."}
  → 엔진이 순차 실행
  → 30s 대기 (pod Ready)
  → _pre_flight_check() 재검증
  → pass: _corrections_applied = True
```

### Trigger 교정 (`_correct_trigger`)

```
실패한 command + 에러 출력
  → Agent 호출 (환경 프로빙: kubectl get deploy, describe-alarms 등)
  → Agent 반환: {"corrected_trigger": {"command": "..."}, "reasoning": "..."}
  → 엔진이 교정된 명령 실행
  → pass: scenario["trigger"]["command"] 업데이트, _corrections_applied = True
```

### Verify Step 교정 (`_bedrock_correct_step`)

```
실패한 step config + detail + 타이밍 정보
  → STEP_CORRECTION 프롬프트 + tool-use (kubectl, aws CLI)
  → Agent 프로빙: 실제 pod 상태, deploy 이름, label 확인
  → Agent 반환: {"corrected_step": {...}, "reasoning": "..."}
    또는: {"skip": true, "reason": "..."}
  → 교정된 step config로 AdaptiveStepRunner.run() 재실행
  → pass: verification step 업데이트, _corrections_applied = True
```

### 교정 프롬프트에 포함되는 타이밍 컨텍스트

```
## 실행 타이밍 정보
- 경과 시간: {elapsed}s
- 폴링 횟수: {polls}회
- 폴링 간격: {poll_interval}s
- 최대 폴링: {max_polls}회
- 현재 settle_delay: {settle_delay}s

## 교정 전략 (우선순위)
1. 타이밍 문제(아직 상태 전이 중) → max_polls/poll_interval 증가
2. expected 값 불일치 → 실제 관찰된 값으로 교정
3. 이미 복구 완료(ex: restartCount=0) → 검증 기준을 현재 상태에 맞게 변경
4. type이 'manual' → 실행 가능한 type(pod_status, kubectl_check)으로 변환
5. step 자체가 의미 없음 → {"skip": true, "reason": "..."}
```

### Agent 앱 비종속성

Agent는 K8s/AWS API만 사용하는 범용 프로빙:
- `kubectl get deploy`, `kubectl get pod -l app=X`
- `aws cloudwatch describe-alarms`
- `aws fis list-experiment-templates`

앱(dockercoins) 전용 로직 없음 — 어떤 K8s/AWS 환경에서도 동작.

---

## 8. 영구 저장 (Persistence)

### 시나리오 교정 영구 반영 (`_persist_corrections`)

```python
def _persist_corrections(self):
    """교정이 적용되고 최종 결과가 pass일 때만 DDB에 시나리오 덮어쓰기."""
    if not self._corrections_applied:
        return
    if self.result not in ("pass", "partial_pass"):
        return
    # 런타임 플래그 제거 후 저장
    _save_scenario(space_id, cleaned_scenario)
```

### 저장 조건

| 조건 | 설명 |
|------|------|
| `_corrections_applied == True` | 최소 1건의 교정이 적용됨 |
| `result in ("pass", "partial_pass")` | 교정 후 실행 성공 |
| 런타임 플래그 제거 | `_correction_attempted`, `_resolved` 등 제거 |

### 효과

- 같은 시나리오 재실행 시 교정 없이 바로 pass (학습된 상태)
- 잘못된 교정은 저장 안 됨 (pass일 때만)
- DDB Key: `run_id: scen-{scenario_id}`

---

## 9. AdaptiveStepRunner (상태 수렴 기반)

### 종료 로직 (시간 기반이 아님)

| 조건 | 결과 |
|------|------|
| `ok == True` | PASS |
| `stale_count >= stale_threshold` | FAIL (시스템 정지 감지) |
| `poll_count >= max_polls` | FAIL (안전 캡) |

### 진행 감지 (Progress Detection)

- 이전 detail ≠ 현재 detail → 진행 신호
- 진행 감지 시 stale 카운터 리셋
- 전이 상태 (Pending, ContainerCreating 등) → stale 면제

### 에러 에스컬레이션

```
step 실패 → _classify_step_error() → ErrorAction 결정
  → RETRY_BACKOFF: 지수 백오프 3회
  → AGENT_CORRECT: Agent 교정 1회
  → POLL_CONTINUE: 추가 폴링
  → TRIGGER_REINJECT: 트리거 재주입 (orchestrator 위임)
  → BLOCKED: 즉시 실패
```

---

## 10. 결과 저장

### DynamoDB 스키마

```
PK: run_id
SK: record_type = "run" | "evaluation"

Fields:
  scenario_id, status, result
  steps: [{name, type, status, detail, error_category, elapsed}]
  incident_id, investigation_task_id
  started_at, completed_at
  evaluation: {passed_criteria, failed_criteria, score}
```

### In-Memory Registry

- `_active_runs[run_id]` → Run 객체
- `list_active_runs()` → running/verifying/executing만
- Thread-safe (`_runs_lock`)

### 상태 전이

```
created → running → verifying → completed (pass/fail/partial)
                  → interrupted
       → preflight_failed
       → self_correcting → completed
```

---

## 11. 평가 (Evaluation)

`/api/evaluate/<run_id>`:
1. Investigation 메시지 수집
2. `evaluate_investigation()` 호출
3. 평가 기준: root_cause_match, causal_chain, data_sources, false_leads
4. DDB에 evaluation 레코드 저장

---

## 12. 주요 함수 참조

| 함수/클래스 | 파일 | 역할 |
|------------|------|------|
| `FAILURE_MODES` | failure_modes.py | FM-01~FM-22 템플릿 정의 |
| `ArchitectureRecommender` | arch_analysis.py:2297 | 아키텍처 기반 시나리오 추천 |
| `ScenarioGenerator` | arch_analysis.py:2454 | 추천 → 실행 가능 시나리오 JSON |
| `GENERATE_SCENARIO_PROMPT` | prompts/generate_scenario.md | 시나리오 생성 프롬프트 |
| `RECOMMEND_PROMPT` | prompts/recommend.md | 추천 프롬프트 |
| `SCENARIO_GEN` | providers/system_prompts.py | 채팅 기반 생성 시스템 프롬프트 |
| `STEP_CORRECTION` | providers/system_prompts.py | 교정 Agent 시스템 프롬프트 |
| `start_run()` | verifier.py:107 | executor 디스패치 + 백그라운드 실행 |
| `SimulationRun` | verifier.py | Classic 실행 상태 머신 |
| `PhasedExecutor` | execution_engine.py:74 | 3-Phase 엔진 (교정 포함) |
| `AdaptiveStepRunner` | engine_step_runner.py:126 | 상태 수렴 기반 step 실행 |
| `EngineResolver` | engine_resolver.py | 변수/리소스 resolution |
| `_correct_trigger()` | execution_engine.py | Trigger 교정 |
| `_correct_preflight()` | execution_engine.py | Preflight 교정 |
| `_bedrock_correct_step()` | execution_engine.py | Verify step 교정 |
| `_persist_corrections()` | execution_engine.py | 교정 시나리오 영구 저장 |
| `_save_scenario()` | routes_arch.py:166 | DDB 시나리오 저장 |
| `api_arch_generate_scenario()` | routes_scenario.py:117 | 시나리오 생성 API 엔드포인트 |
| `api_scenario_run()` | routes_scenario.py:936 | 시나리오 실행 API 엔드포인트 |

---

## 13. 설계 원칙

1. **Fail-fast resolution**: 실행 전에 변수/리소스 미존재를 감지하여 무의미한 실행 방지
2. **적응형 확인**: blind sleep 대신 상태 수렴 감지로 상황에 맞는 대기
3. **보장된 정리**: Cleanup registry로 어떤 실패에서도 생성 리소스 정리
4. **단일 교정**: phase당 1회만 교정 시도 → 무한 루프 방지
5. **Phase guard**: trigger_active → effect_observed → reaction_confirmed 순서 강제
6. **교정 = 재생성**: 동일 Agent가 실제 환경 기반으로 정확한 시나리오 조각 생성
7. **Pass-only persistence**: 교정 후 pass일 때만 영구 저장 (잘못된 교정 확산 방지)
8. **앱 비종속**: Agent는 K8s/AWS API만 사용, 특정 앱 로직 없음
