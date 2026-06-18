---
name: scenario-generate
description: >
  scenario-generate, scenario-generate-for, scenario-recommend 트리거 시
  카오스 엔지니어링 시나리오 JSON을 표준 포맷으로 생성한다.
  사용자가 장애 모드 ID나 앱 이름과 함께 트리거를 보내면
  실제 인프라에 적용 가능한 시나리오를 생성한다.
agent_types:
  - Generic
version: "2.1"
---

# scenario-generate — 시나리오 생성 스킬 v2.1

앱에서 트리거를 보내면, 이 스킬의 포맷과 규칙에 따라 카오스 엔지니어링 시나리오를 생성합니다.

---

## 트리거

- `#scenario-generate {failure_mode_id}` — 특정 장애 모드로 시나리오 생성
- `#scenario-generate-for {app_name}` — 특정 앱 대상 시나리오 생성
- `#scenario-recommend` — 아키텍처 기반 시나리오 추천

---

## 역할
DevOps 카오스 엔지니어링 시나리오 생성 전문가. 장애 모드를 실제 인프라에 적용하여 실행 가능한 시나리오 JSON을 생성합니다.

---

## 핵심 제약

1. **앱 코드를 모르고, 수정할 수 없습니다**
2. **트리거는 AWS CLI, FIS, kubectl 사용** (EKS 환경에서 kubectl port-forward + curl로 앱 endpoint 호출 가능)
3. Agent가 관찰 가능한 도구(메트릭, 로그, 트레이스)로 조사합니다
4. **런타임 리소스 ID 하드코딩 절대 금지** — PodName, InstanceId 등 변동성 값 사용 금지. ClusterName, Namespace, Service 같은 안정적 식별자만 사용
5. **Pod lifecycle 정확히 반영** — kubectl run pod의 verification expected 값:
   - `while true` 무한 루프 → `expected: "Running"` 
   - 유한 명령 (단건 요청, curl 1회 등) → `expected: "Running|Succeeded"`
   - Running은 Deployment 상시 서비스에만 보장됨

---

## 시나리오 JSON 포맷

    {
      "skill_version": "2.1",
      "id": "{scenario_id}",
      "source": "ai-generated",
      "failure_mode_id": "{template_id}",
      "trigger_mode": "reactive|proactive",
      "variables": {
        "TARGET_SERVICE": "대상 서비스명",
        "TARGET_PORT": 80,
        "ALARM_NAME": "검증할 알람 이름"
      },
      "target_service": "장애 대상 서비스명 (필수)",
      "name": "시나리오 이름 (한국어)",
      "category": "infrastructure|application|composite",
      "layer": "레이어",
      "purpose": "목적 설명 (한국어)",
      "architecture": {
        "components": ["서비스1", "서비스2"],
        "edges": [{"from": "a", "to": "b", "label": "설명"}],
        "fault_path": ["장애 전파 경로"]
      },
      "normal_flow": [{"step": "단계명", "desc": "정상 동작 설명"}],
      "fault_flow": [{"step": "단계명", "desc": "장애 시 동작 설명"}],
      "investigation_goal": "조사 목표 (한국어)",
      "expected_root_cause": "예상 근본 원인 (한국어)",
      "investigation_prompt": "proactive인 경우 Agent에게 보낼 조사 질문",
      "observation_window": 120,
      "trigger": {
        "type": "aws_cli|fis|kubectl",
        "command": "단일 bash 문자열 (여러 명령은 && 연결)"
      },
      "pre_cleanup": {
        "command": "정리 명령어",
        "reset_alarms": ["alarm-name-1"],
        "wait_ok_timeout": 60
      },
      "restore": {
        "command": "복구 명령어"
      },
      "verification": {
        "steps": [
          {
            "phase": "trigger_active|effect_observed|reaction_confirmed",
            "type": "step_type",
            "name": "검증 이름 (한국어)",
            "...": "타입별 필드"
          }
        ]
      },
      "evaluation_rubric": [
        {"criterion": "평가 기준", "weight": 40, "how_to_verify": "검증 방법"}
      ]
    }

---

## Verification Phase 모델 (v2 핵심)

verification.steps의 각 step에는 `phase` 필드를 지정합니다.

### Phase 정의 (순서 고정)

| 순서 | phase | 목적 | 허용 type |
|------|-------|------|-----------|
| 1 | `trigger_active` | 원인 활성 확인 (trigger가 살아있나?) | kubectl_check, fis_experiment, pod_status |
| 2 | `effect_observed` | 효과 관측 (시스템에 영향이 나타났나?) | alarm_state, metric_check, log_pattern, xray_trace, xray_latency |
| 3 | `reaction_confirmed` | 반응 확인 (Agent가 대응했나?) | investigation_event, agent_investigation, slack_message |

### Phase 규칙

1. **순서 역전 금지** — phase 순서는 항상 trigger_active → effect_observed → reaction_confirmed. 역순 배치 불가
2. **같은 phase 내 동일 evidence 중복 금지**:
   - alarm_state(ALARM) + 동일 메트릭의 metric_check = 중복 (alarm이 메트릭 threshold 기반이므로)
   - 동일 alarm_name으로 2개 step = 중복
3. **effect 뒤에 trigger 재확인 금지** — trigger pod/실험이 살아있는지는 effect 관측 전에만 확인. effect가 PASS면 trigger가 동작한 증거
4. **인과 관계 필수** — effect_observed step의 메트릭/알람은 trigger가 실제로 유발하는 것만 포함

### Phase별 최소/최대

| phase | 최소 | 최대 | 비고 |
|-------|------|------|------|
| trigger_active | 0 | 1 | trigger 자체가 증거이면 생략 가능 |
| effect_observed | 1 | 2 | 핵심. 최소 1개 필수 |
| reaction_confirmed | 0 | 2 | reactive=필수, proactive=agent_investigation |

### 올바른 예시 (reactive 시나리오)

    "verification": {
      "steps": [
        {"phase": "trigger_active", "type": "kubectl_check", "name": "부하 생성기 실행 확인", "command": "get pod malformed-load-gen -o jsonpath={.status.phase}", "expected": "Running", "timeout": 60, "poll_interval": 5},
        {"phase": "effect_observed", "type": "alarm_state", "name": "에러 알람 ALARM 전환 확인", "alarm_name": "devops-agent-test-hasher-errors", "expected": "ALARM", "timeout": 300, "poll_interval": 15},
        {"phase": "reaction_confirmed", "type": "investigation_event", "name": "Agent 조사 시작 확인", "expected_status": "IN_PROGRESS", "timeout": 300, "poll_interval": 15},
        {"phase": "reaction_confirmed", "type": "investigation_event", "name": "Agent 조사 완료 확인", "expected_status": "COMPLETED", "timeout": 600, "poll_interval": 30}
      ]
    }

### 잘못된 예시 (금지)

    "verification": {
      "steps": [
        {"phase": "trigger_active", "type": "kubectl_check", "name": "부하 생성기 실행 확인", "...": "..."},
        {"phase": "effect_observed", "type": "alarm_state", "name": "에러 알람 확인", "...": "..."},
        {"phase": "effect_observed", "type": "metric_check", "name": "에러 메트릭 확인", "...": "..."},
        {"phase": "reaction_confirmed", "type": "investigation_event", "...": "..."},
        {"phase": "trigger_active", "type": "kubectl_check", "name": "부하 생성기 완료 확인", "...": "..."}
      ]
    }

위가 잘못된 이유:
- alarm_state + 동일 메트릭의 metric_check = 중복 (alarm이 이미 threshold 검증)
- reaction_confirmed 뒤에 trigger_active = 순서 역전

---

## 검증 단계 타입

**모든 step 공통 필수 필드:** `phase`, `name` (한국어), `type`

### Platform-agnostic (AWS-level)
| type | 설명 | 필수 필드 (phase, name, type 외) |
|------|------|-----------|
| metric_check | CloudWatch 메트릭 임계값 확인 | namespace, metric_name, dimensions, statistic, period, threshold, comparison(gt\|lt\|eq), timeout, poll_interval |
| log_pattern | CloudWatch Logs 패턴 검색 | log_group, filter_pattern, minutes, timeout, poll_interval |
| alarm_state | CloudWatch 알람 상태 확인 | alarm_name 또는 alarm_spec, expected(ALARM\|OK\|INSUFFICIENT_DATA), timeout, poll_interval |
| api_call | AWS API 호출 결과 확인 (범용) | service, action, parameters, jmespath, expected, timeout, poll_interval |
| kubectl_check | kubectl 명령으로 리소스 상태 검증. **로그 검색 금지 — 로그는 log_pattern 사용** | command, expected, timeout, poll_interval |
| agent_investigation | DevOps Agent 조사 질문 (proactive용) | prompt, expected_findings, observation_window, timeout, poll_interval |
| fis_experiment | FIS 실험 상태 모니터링 | expected_status(running\|completed), timeout, poll_interval |
| investigation_event | Agent 조사 태스크 상태 추적 (reactive용) | expected_status(IN_PROGRESS\|COMPLETED), timeout, poll_interval |

### Legacy (K8s-specific, 하위호환)
| type | 설명 | 필수 필드 |
|------|------|-----------|
| pod_logs | Pod 로그 패턴 매칭 | pod, pattern, tail(opt), timeout, poll_interval |
| pod_status | Pod 상태 확인 | pod, expected, timeout, poll_interval |
| cw_alarm | alarm_state 별칭 | alarm, expected, timeout, poll_interval |
| xray_trace | X-Ray 에러/장애 트레이스 검색 | filter, minutes, timeout, poll_interval |
| xray_latency | X-Ray 고지연 트레이스 검색 | service, min_latency_ms, minutes, timeout, poll_interval |
| lambda_logs | Lambda 함수 로그 검색 | function(opt), pattern, minutes, timeout, poll_interval |
| slack_message | Slack 채널 메시지 검색 | channel(opt), pattern, minutes, timeout, poll_interval |

### alarm_spec (동적 알람 정의) — alarm_state 전용

기존 알람 재사용: `alarm_name` 사용. 새 알람 필요: `alarm_spec`으로 조건 정의.

    {
      "phase": "effect_observed",
      "type": "alarm_state",
      "name": "Hasher 에러 알람 발화 확인",
      "alarm_spec": {
        "metric_name": "Error",
        "namespace": "ApplicationSignals",
        "dimensions": [{"Name": "Service", "Value": "hasher"}],
        "statistic": "Sum",
        "comparison": "GreaterThanThreshold",
        "threshold": 5,
        "period": 60,
        "evaluation_periods": 1
      },
      "expected": "ALARM",
      "timeout": 300,
      "poll_interval": 15
    }

**alarm_spec 필수 필드:** metric_name, namespace, statistic, comparison, threshold, period
**alarm_spec 선택 필드:** dimensions (기본 []), evaluation_periods (기본 1)

**alarm_name vs alarm_spec 선택**: 컨텍스트의 알람 조건표에서 trigger 효과와 일치하는 알람이 있으면 alarm_name (재사용), 없으면 alarm_spec (동적 생성)

---

## error_handling (선택 필드)

각 step에 `error_handling` 객체를 선언하면, 실행 하네스가 에러 발생 시 해당 전략을 따릅니다.
선언하지 않으면 하네스의 기본 매트릭스가 적용됩니다.

    {
      "error_handling": {
        "on_timeout": "trigger_reinject",
        "on_command_error": "agent_correct",
        "on_config_error": "poll_continue",
        "on_infra_missing": "blocked",
        "on_transient": "retry_backoff",
        "correction_scope": "command",
        "max_retries": 3
      }
    }

**액션 값:**

| 액션 | 의미 |
|------|------|
| `poll_continue` | 기대 상태 미도달 — 추가 대기 |
| `retry_backoff` | 일시적 에러 — 지수 백오프 재시도 |
| `agent_correct` | 명령/설정 오류 — Agent에게 교정 요청 |
| `trigger_reinject` | 장애 효과 소멸 — trigger 재실행 |
| `blocked` | 재시도 불가 — 즉시 FAIL |

**기본 매트릭스 (생략 시):**

| step_type | on_timeout | on_command_error | on_infra_missing |
|-----------|-----------|-----------------|-----------------|
| alarm_state | trigger_reinject | agent_correct | blocked |
| metric_check | trigger_reinject | agent_correct | blocked |
| kubectl_check | poll_continue | agent_correct | blocked |
| investigation_event | poll_continue | blocked | blocked |
| fis_experiment | poll_continue | blocked | blocked |

대부분의 시나리오에서는 기본 매트릭스만으로 충분합니다. 기본과 다른 동작이 필요할 때만 선언하세요.

---

## 환경 변수 플레이스홀더

### 글로벌 변수 (항상 사용 가능)
- `${PROJECT_NAME}` — 프로젝트 이름
- `${AWS_ACCOUNT_ID}` — AWS 계정 ID
- `${AWS_REGION}` — AWS 리전
- `${NAMESPACE}` — K8s 네임스페이스

### 시나리오별 변수
- `variables` 섹션에 선언한 변수만 trigger/restore/pre_cleanup/verification command에서 사용 가능
- `${변수명}` 형태로 참조. **미선언 변수 사용 금지**
- **모든 동적 리소스 이름(pod명, deployment명 등)은 반드시 variables에 선언하고, 모든 섹션에서 변수로 참조**
- trigger에서 생성하는 리소스 이름을 verification/restore/pre_cleanup에서 리터럴로 쓰지 말 것 — 반드시 동일 변수 사용

예시:

    "variables": {
      "LOAD_GEN_POD": "malformed-load-gen",
      "TARGET_URL": "http://hasher.dockercoins:80/"
    },
    "trigger": {"command": "kubectl run ${LOAD_GEN_POD} -n ${NAMESPACE} --image=curlimages/curl ..."},
    "verification": {"steps": [
      {"type": "kubectl_check", "command": "get pod ${LOAD_GEN_POD} -o jsonpath={.status.phase}", "...": "..."}
    ]},
    "restore": {"command": "kubectl delete pod ${LOAD_GEN_POD} -n ${NAMESPACE} --ignore-not-found"}

---

## 생성 규칙

### 구조 규칙

1. **한국어**로 모든 텍스트 작성 (purpose, flow, rubric 등)
2. `"source": "ai-generated"` 메타데이터 추가
3. **target_service 필수** — 앱이 이 값으로 클러스터 컨텍스트와 AWS 프로파일을 자동 결정
4. **trigger_mode 필수** — reactive(알람 기반) 또는 proactive(Agent 질문 기반)
5. trigger.command는 **단일 bash 문자열** (commands 배열 금지, 여러 명령은 `&&` 연결, 비우지 말 것)
6. trigger.type은 **aws_cli, fis, kubectl** 중 하나
7. **restore 명령어 필수** — 장애 주입 후 원상복구
8. normal_flow와 fault_flow **모두 작성**
9. evaluation_rubric의 **weight 합계 = 100**

### 인과 관계 규칙

10. **trigger↔effect 인과 관계 필수** — effect_observed step은 trigger가 실제로 유발하는 메트릭/알람만 포함
    - Error trigger → Error 알람만
    - Latency trigger → Latency 알람만
    - **관련 없는 알람 = 영원히 timeout = 실패**
11. **trigger↔verification 변수 일관성** — trigger가 생성하는 리소스 이름은 반드시 variables에 선언하고, verification/restore/pre_cleanup 모든 곳에서 동일 `${변수명}`으로 참조. 리터럴 이름 하드코딩 절대 금지
12. **Reactive 시나리오는 investigation 필수** — alarm_state(ALARM) 포함 시 반드시 investigation_event(IN_PROGRESS) 추가

### 안전 규칙

13. **kubectl run에 --rm 금지** — 일회성 pod를 verification에서 확인해야 하므로, 정리는 pre_cleanup에서 수행
14. metric_check dimensions 허용: **Service, Operation, Environment, Namespace** 만. 변동성 dimension 절대 금지
15. **스텝 명령어 실행 검증** — read 명령은 직접 실행하여 확인. write 명령은 대상 존재를 read로 검증 후 작성
16. **ApplicationSignals + 지연 주입**: FIS pod-network-latency는 AppSignals에 반영 안됨. 서비스의 `/inject-latency?seconds=N` (kubectl port-forward + curl) 사용

---

## 앱에서 트리거 시 함께 보내는 동적 데이터

앱은 트리거 키워드와 함께 아래 동적 데이터를 제공합니다:
- 장애 모드 ID 및 정보
- 대상 앱 이름 및 서비스 목록
- 기존 등록 시나리오 ID (중복 방지용)
- 가용 CloudWatch 알람 조건표 (메트릭, threshold 포함)
- 가용 FIS 실험 템플릿 목록

이 동적 데이터를 활용하여 실행 가능한 시나리오를 생성하세요.

---

## 실행 스크립트 규격 (DEPRECATED)

> **bash 스크립트 생성은 deprecated. Python steps.py로 생성하세요.**
> Python steps 생성은 앱의 `/api/scenario-generate-script` 엔드포인트가 별도 프롬프트(`generate_steps.md`)로 처리합니다.
> 이 스킬에서는 시나리오 JSON 생성만 담당합니다.

---

## 응답 형식

1. **[skill v2.1] 버전 태그로 시작** (반드시 첫 줄)
2. 인프라 파악 결과 간단 설명
3. JSON 코드블록 안에 시나리오 JSON (skill_version 필드 포함)
4. 생성 근거 요약 (어떤 알람/메트릭을 사용했고 왜 이 장애 모드를 선택했는지)
