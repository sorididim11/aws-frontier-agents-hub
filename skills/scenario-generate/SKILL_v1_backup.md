---
name: scenario-generate
description: >
  카오스 엔지니어링 시나리오 생성 요청 시 적용. 사용자가 #scenario-generate,
  #scenario-generate-for, 또는 #scenario-recommend 트리거를 보내면 장애 모드를
  실제 인프라에 적용하여 실행 가능한 시나리오 JSON을 표준 포맷으로 생성한다.
  트리거 명령, 검증 단계, 복구 절차를 포함한 완전한 시나리오를 제공한다.
agent_types:
  - Generic
---

# scenario-generate — 시나리오 생성 스킬

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

## 핵심 제약 (반드시 준수)

1. **앱 코드를 모르고, 수정할 수 없습니다**
2. **트리거는 AWS CLI, FIS, kubectl 사용** (EKS 환경에서 kubectl port-forward + curl로 앱 endpoint 호출 가능)
3. Agent가 관찰 가능한 도구(메트릭, 로그, 트레이스)로 조사합니다
4. **런타임 리소스 ID 하드코딩 절대 금지:**
   - PodName (pod는 재배포 시 이름 변경)
   - InstanceId (노드 교체 시 변경)
   - metric_check dimensions은 ClusterName, Namespace 같은 **안정적 식별자만** 사용
   - architecture에 pod명, instance-id 등 런타임 값 포함 금지
5. **Pod lifecycle 정확히 반영:**
   - kubectl run 일회성 pod (load generator 등) → 완료 후 `Succeeded` 상태 (Running 아님)
   - kubectl_check expected="Running"은 Deployment 상시 서비스에만
   - 일회성 작업은 expected="Succeeded"

---

## 시나리오 JSON 포맷

```json 코드블록 안에 작성할 것:

```json
{
  "id": "{scenario_id}",
  "source": "ai-generated",
  "failure_mode_id": "{template_id}",
  "trigger_mode": "reactive|proactive",
  "variables": {
    "TARGET_SERVICE": "대상 서비스명 (예: hasher)",
    "TARGET_PORT": 80,
    "ALARM_NAME": "검증할 알람 이름",
    "LATENCY_SECONDS": 5
  },
  "target_service": "장애 대상 서비스명 (필수, 예: hasher, worker, rng)",
  "name": "시나리오 이름 (한국어)",
  "category": "infrastructure|application|composite",
  "layer": "레이어",
  "purpose": "목적 설명 (한국어)",
  "architecture": {
    "components": ["서비스1", "서비스2"],
    "edges": [{"from": "a", "to": "b", "label": "설명"}],
    "fault_path": ["장애 전파 경로"]
  },
  "normal_flow": [
    {"step": "단계명", "desc": "정상 동작 설명"}
  ],
  "fault_flow": [
    {"step": "단계명", "desc": "장애 시 동작 설명"}
  ],
  "investigation_goal": "조사 목표 (한국어)",
  "expected_root_cause": "예상 근본 원인 (한국어)",
  "investigation_prompt": "proactive인 경우 Agent에게 보낼 조사 질문",
  "observation_window": 120,
  "trigger": {
    "type": "aws_cli|fis|kubectl",
    "command": "단일 bash 문자열 (여러 명령은 && 연결, 스크립트 모드에서도 비우지 말 것)"
  },
  "pre_cleanup": {
    "command": "정리 명령어",
    "reset_alarms": ["alarm-name-1", "alarm-name-2"],
    "wait_ok_timeout": 60
  },
  "restore": {
    "command": "복구 명령어"
  },
  "verification": {
    "steps": [
      {
        "type": "step_type",
        "name": "검증 이름 (한국어)",
        "config_fields": "타입별 설정"
      }
    ]
  },
  "evaluation_rubric": [
    {"criterion": "평가 기준", "weight": 40, "how_to_verify": "검증 방법"}
  ]
}
```

---

## 검증 단계 타입 (verification.steps에 사용)

**모든 step 공통 필수 필드:** `name` (한국어 라벨), `type`
- `name`은 UI에 표시되는 검증 단계 이름입니다. `description` 사용 금지 — 반드시 `name`.

### error_handling (에러 대응 선언) — 선택 필드

각 step에 `error_handling` 객체를 선언하면, 실행 하네스가 에러 발생 시 해당 전략을 따릅니다.
선언하지 않으면 하네스의 기본 매트릭스(step_type × error_category)가 적용됩니다.

```json
{
  "type": "alarm_state",
  "name": "Hasher 에러 알람 트리거",
  "alarm_name": "...",
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
```

**사용 가능한 액션 값:**

| 액션 | 의미 |
|------|------|
| `poll_continue` | 아직 기대 상태 미도달 — 추가 대기 (timeout 연장 1회) |
| `retry_backoff` | 일시적 에러 — 지수 백오프 재시도 (5s→10s→20s, 최대 3회) |
| `agent_correct` | 명령/설정 오류 — Agent에게 교정 요청 후 재검증 |
| `trigger_reinject` | 장애 효과 소멸 — trigger 재실행 후 추가 polling |
| `blocked` | 재시도 불가 — 즉시 FAIL |

**correction_scope** (agent_correct 시):
- `"command"` — kubectl/CLI 명령만 교정 (기본값)
- `"config"` — 전체 step config 교정 (api_call의 jmespath, parameters 등)

**기본값 (error_handling 생략 시 적용되는 매트릭스):**

| step_type | on_timeout | on_command_error | on_config_error | on_infra_missing | on_transient |
|-----------|-----------|-----------------|----------------|-----------------|-------------|
| alarm_state / cw_alarm | trigger_reinject | agent_correct | poll_continue | blocked | retry_backoff |
| metric_check | trigger_reinject | agent_correct | poll_continue | blocked | retry_backoff |
| kubectl_check | poll_continue | agent_correct | poll_continue | blocked | retry_backoff |
| api_call | agent_correct | agent_correct | agent_correct | blocked | retry_backoff |
| pod_status / pod_logs | poll_continue | agent_correct | poll_continue | blocked | retry_backoff |
| fis_experiment | poll_continue | blocked | poll_continue | blocked | retry_backoff |
| investigation_event | poll_continue | blocked | poll_continue | blocked | retry_backoff |
| agent_investigation | poll_continue | blocked | poll_continue | blocked | retry_backoff |

**언제 명시적으로 선언해야 하는가:**
- 기본 매트릭스와 다른 동작이 필요할 때 (예: kubectl_check인데 timeout 시 trigger_reinject가 필요한 경우)
- max_retries를 기본(3)이 아닌 값으로 설정할 때
- correction_scope를 config로 지정해야 할 때 (api_call의 jmespath 교정 등)

### Platform-agnostic (AWS-level)
| type | 설명 | 필수 필드 (name, type 외) |
|------|------|-----------|
| metric_check | CloudWatch 메트릭 임계값 확인 | namespace, metric_name, dimensions, statistic, period, threshold, comparison(gt\|lt\|eq), timeout, poll_interval |
| log_pattern | CloudWatch Logs 패턴 검색 | log_group, filter_pattern, minutes, timeout, poll_interval |
| alarm_state | CloudWatch 알람 상태 확인 | alarm_name 또는 alarm_spec, expected(ALARM\|OK\|INSUFFICIENT_DATA), timeout, poll_interval |
| api_call | AWS API 호출 결과 확인 (범용) | service, action, parameters, jmespath, expected, timeout, poll_interval |
| kubectl_check | kubectl 명령으로 리소스 상태 검증 (예: pod phase=Running). **로그 검색 금지 — 로그는 log_pattern 사용** | command(예: `get pods -l app=hasher -o jsonpath={.items[0].status.phase}`), expected(예: Running, Succeeded), timeout, poll_interval |
| agent_investigation | DevOps Agent 조사 질문 (proactive용) | prompt, expected_findings, observation_window, timeout, poll_interval |
| fis_experiment | FIS 실험 상태 모니터링 | expected_status(running\|completed), timeout, poll_interval |
| investigation_event | Agent 조사 태스크 상태 추적 (reactive용) | expected_status(IN_PROGRESS\|COMPLETED), timeout, poll_interval |

### alarm_spec (동적 알람 정의) — alarm_state 스텝 전용

기존 알람을 참조할 때는 `alarm_name`을 사용하고, 새 알람이 필요하면 `alarm_spec`으로 조건을 정의합니다.
런타임 하네스가 alarm_spec을 보고 동일 조건 기존 알람을 재사용하거나, 없으면 동적 생성합니다.

**alarm_name 사용 (기존 알람 재사용):**
```json
{
  "type": "alarm_state",
  "name": "Hasher 에러 알람 발화 확인",
  "alarm_name": "devops-agent-test-hasher-errors",
  "expected": "ALARM",
  "timeout": 300,
  "poll_interval": 15
}
```

**alarm_spec 사용 (동적 알람 생성 요청):**
```json
{
  "type": "alarm_state",
  "name": "Hasher 에러 알람 발화 확인",
  "alarm_spec": {
    "metric_name": "Error",
    "namespace": "ApplicationSignals",
    "dimensions": [
      {"Name": "Service", "Value": "hasher"},
      {"Name": "Operation", "Value": "POST /"}
    ],
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
```

**alarm_spec 필수 필드:**

| 필드 | 설명 | 예시 |
|------|------|------|
| metric_name | CloudWatch 메트릭 이름 | Error, Fault, Latency |
| namespace | 메트릭 네임스페이스 | ApplicationSignals, ContainerInsights |
| statistic | 집계 방식 | Sum, Average, Maximum |
| comparison | 비교 연산자 | GreaterThanThreshold, LessThanThreshold |
| threshold | 임계값 | 5, 500 |
| period | 평가 기간(초) | 60, 300 |

**alarm_spec 선택 필드:**

| 필드 | 설명 | 기본값 |
|------|------|--------|
| dimensions | 메트릭 차원 배열 | [] |
| evaluation_periods | 연속 위반 횟수 | 1 |

### Legacy (K8s-specific)
| type | 설명 | 필수 필드 |
|------|------|-----------|
| pod_logs | Pod 로그 패턴 매칭 | pod, pattern, tail(opt), timeout, poll_interval |
| pod_status | Pod 상태 확인 | pod, expected(OOMKilled\|CrashLoopBackOff\|...\|Running), timeout, poll_interval |
| cw_alarm | CloudWatch 알람 상태 (alarm_state 별칭) | alarm(${PROJECT_NAME}-xxx), expected(ALARM\|OK), timeout, poll_interval |
| xray_trace | X-Ray 에러/장애 트레이스 검색 | filter(X-Ray filter expr), minutes, timeout, poll_interval |
| xray_latency | X-Ray 고지연 트레이스 검색 | service, min_latency_ms, minutes, timeout, poll_interval |
| lambda_logs | Lambda 함수 로그 검색 | function(opt), pattern, minutes, timeout, poll_interval |
| slack_message | Slack 채널 메시지 검색 | channel(opt), pattern, minutes, timeout, poll_interval |
| manual | 수동 확인 대기 | timeout |

---

## 환경 변수 플레이스홀더

### 글로벌 변수 (항상 사용 가능)
- `${PROJECT_NAME}` — 프로젝트 이름
- `${AWS_ACCOUNT_ID}` — AWS 계정 ID
- `${AWS_REGION}` — AWS 리전
- `${NAMESPACE}` — K8s 네임스페이스

### 시나리오별 변수
- 시나리오 JSON의 `variables` 섹션에 선언한 변수만 trigger/restore/pre_cleanup command에서 사용 가능
- `${변수명}` 형태로 참조 (예: `${TARGET_SERVICE}`, `${ALARM_NAME}`, `${LATENCY_SECONDS}`)
- **variables에 선언하지 않은 변수 사용 금지**

---

## 생성 규칙 (24개)

1. **한국어**로 모든 텍스트 작성 (purpose, flow, rubric 등)
2. trigger.command에 **플레이스홀더** 사용 (글로벌 또는 variables에 선언된 변수)
3. trigger.type은 **aws_cli, fis, kubectl** 중 하나. kubectl은 EKS 환경에서 port-forward + curl로 앱 endpoint 호출 시 사용
4. **restore 명령어 필수** — 장애 주입 후 원상복구
5. evaluation_rubric의 **weight 합계 = 100**
6. verification.steps에 **최소 3단계** 포함
7. `"source": "ai-generated"` 메타데이터 추가
8. pre_cleanup으로 이전 상태 정리 (reset_alarms 포함)
9. normal_flow와 fault_flow **모두 작성**
10. **trigger_mode 필수** — reactive(알람 기반) 또는 proactive(Agent 질문 기반)
11. proactive 시나리오: `investigation_prompt`, `observation_window`(초) 필드 포함
12. trigger.command는 **단일 문자열** (commands 배열 금지, 여러 명령은 `&&` 연결)
13. 환경 변수: **글로벌 변수 + variables에 선언한 변수만** 허용. 미선언 변수 금지
14. trigger가 생성하는 리소스 이름 = verification.steps에서 참조하는 이름 (**일치 필수**)
15. kubectl run으로 pod 생성 시, verification의 kubectl_check에서 **동일한 pod 이름** 사용
16. metric_check dimensions 허용: **Service, Operation, Environment, Namespace** 만. PodName, InstanceId, NodeName 등 변동성 dimension 절대 금지
17. kubectl run 일회성 pod의 kubectl_check expected는 **Succeeded** (Running 아님)
18. **target_service 필수** — 장애 대상 서비스명 (최상위 필드). 앱이 이 값으로 클러스터 컨텍스트와 AWS 프로파일을 자동 결정
19. **trigger.command 비우지 말 것** — 스크립트 모드(#include-script)에서도 장애 주입의 핵심 명령을 trigger.command에 기록. 앱이 이 필드에서 대상 서비스, 계정을 파싱하여 실행 환경을 결정
20. **kubectl run에 --rm 금지** — 일회성 pod를 verification에서 확인해야 하므로 --rm 사용 불가. pod 정리는 pre_cleanup에서 수행
21. **스텝 명령어 실행 검증** — 시나리오의 trigger.command, verification.steps, restore.command에 넣을 명령어를 작성할 때:
    - **read 명령**(kubectl get, aws describe-*, aws cloudwatch describe-alarms 등)은 직접 실행하여 동작을 확인. 실패하면 원인을 파악하고 명령을 수정하여 재실행. 성공한 명령만 시나리오에 포함
    - **write 명령**(delete, revoke, inject 등)은 실행할 수 없으므로, 해당 명령이 조작할 대상 리소스가 실제로 존재하는지 read 명령으로 철저히 검증한 뒤 작성
22. **alarm_spec vs alarm_name 선택 기준** — verification에 alarm_state를 넣을 때:
    - 컨텍스트에 제공된 **알람 조건표**에서 trigger 효과와 일치하는 알람이 있으면 → `alarm_name` (재사용)
    - 일치하는 알람이 없거나, 새 앱/메트릭 조합이면 → `alarm_spec` (동적 생성 요청)
    - alarm_spec 작성 시 반드시 trigger가 유발하는 메트릭만 사용
23. **trigger↔alarm 인과 관계 필수** — alarm_state 스텝 작성 시:
    - 알람 메트릭 조건표를 확인하고, trigger가 실제로 해당 메트릭을 유발하는 경우만 포함
    - Error 유발 trigger → Error 메트릭 알람만 (Fault, Latency 알람은 별개 메트릭)
    - Latency 유발 trigger → Latency 메트릭 알람만
    - CPU stress → CPU/Memory 알람만
    - **관련 없는 알람을 넣으면 영원히 timeout되어 실패** — 절대 금지
24. **Reactive 시나리오는 investigation 스텝 필수** — trigger_mode=reactive일 때:
    - alarm_state(expected=ALARM) 포함 시 반드시 investigation_event 스텝 추가
    - investigation_event 2개: IN_PROGRESS (조사 시작 확인) + COMPLETED (조사 완료 확인)
    - 순서: alarm_state(ALARM) → investigation_event(IN_PROGRESS) → investigation_event(COMPLETED) → alarm_state(OK)

---

## 추가 검증 규칙 (시나리오 유효성)

- trigger.command는 단일 문자열 (commands 배열 금지, 여러 명령은 && 연결)
- trigger가 생성하는 리소스 이름 = verification이 참조하는 이름 (일치 필수)
- evaluation_rubric weight 합계 = 100
- metric_check dimensions에 PodName, InstanceId 등 변동성 값 사용 금지

---

## 앱에서 트리거 시 함께 보내는 동적 데이터

앱은 트리거 키워드와 함께 아래 동적 데이터를 제공합니다:
- 장애 모드 ID 및 정보
- 대상 앱 이름 및 서비스 목록
- 기존 등록 시나리오 ID (중복 방지용)
- 가용 CloudWatch 알람 목록
- 가용 FIS 실험 템플릿 목록

이 동적 데이터를 활용하여 실행 가능한 시나리오를 생성하세요.

---

## 실행 스크립트 규격 (`#include-script` 요청 시)

사용자가 `#include-script`를 포함하면, 시나리오 JSON 다음에 bash 실행 스크립트를 생성합니다.

### 스크립트 표준 구조

```bash
#!/bin/bash
set -e
export AWS_PROFILE="${AWS_PROFILE:-member1-acc}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
NAMESPACE="${NAMESPACE:-dockercoins}"

STEP=0
PASSED=0
TOTAL=<총 step 수>

checkpoint() {
  STEP=$1
  local name="$2" status="$3" detail="$4"
  echo "CHECKPOINT|$STEP|$name|$status|$detail"
  if [ "$status" = "PASS" ]; then PASSED=$((PASSED+1)); fi
}

# --- Step 1: 환경 사전 확인 ---
checkpoint 1 "환경 사전 확인" "PASS|FAIL" "상세 내용"

# --- Step 2: Trigger (장애 주입) ---
checkpoint 2 "장애 주입" "PASS|FAIL" "상세 내용"

# --- Step 3~N: Verification ---
checkpoint N "step 이름" "PASS|FAIL" "상세 내용"

# --- 최종 결과 ---
echo "RESULT|$PASSED/$TOTAL"
if [ "$PASSED" -eq "$TOTAL" ]; then exit 0; else exit 1; fi
```

### 스크립트 규칙

1. bash 언어만 사용 (python 스크립트 금지)
2. **checkpoint 함수 필수** — 각 step 결과를 `CHECKPOINT|N|name|status|detail` 형식 출력
3. bash 3 호환: `declare -A` 금지, `set -euo pipefail` 대신 `set -e`
4. wget 금지 → curl 사용
5. JSON 파싱은 `python3 -c "import json,sys; ..."` 인라인 사용
6. FIS 실험: `aws fis start-experiment --experiment-template-id <ID>`
7. alarm polling: `aws cloudwatch describe-alarms --alarm-names <name> --query 'MetricAlarms[0].StateValue' --output text`
8. kubectl은 `kubectl -n $NAMESPACE` 형태
9. **ApplicationSignals alarm + 지연 주입 시**: FIS pod-network-latency는 커널(tc netem) 레벨이라 ApplicationSignals Latency에 반영 안됨. 서비스의 `/inject-latency?seconds=N` → `kubectl port-forward + curl`로 호출. 복원은 `/clear-latency` 호출
10. 최종 결과는 `echo "RESULT|$PASSED/$TOTAL"` 형식

### 안정성 규칙: 보수적 타임아웃 & Retry (반드시 준수)

CloudWatch 메트릭은 수집→집계→평가까지 1~3분 지연이 있으므로, 스크립트는 **보수적 타임아웃과 retry 로직**을 필수로 포함해야 합니다.

#### 1. 알람 설정 동적 조회 (Step 1에서 수행)
스크립트 시작 시 `aws cloudwatch describe-alarms`로 알람의 `Period`, `EvaluationPeriods`, `Threshold`를 조회하고 타임아웃을 **동적 계산**합니다.

```bash
ALARM_INFO=$(aws cloudwatch describe-alarms --alarm-names "$ALARM_NAME" \
  --query 'MetricAlarms[0].{Threshold:Threshold,Period:Period,EvalPeriods:EvaluationPeriods}' \
  --output json)
PERIOD=$(echo "$ALARM_INFO" | python3 -c "import json,sys; print(json.load(sys.stdin).get('Period',60))")
EVAL_PERIODS=$(echo "$ALARM_INFO" | python3 -c "import json,sys; print(json.load(sys.stdin).get('EvalPeriods',1))")
THRESHOLD=$(echo "$ALARM_INFO" | python3 -c "import json,sys; print(json.load(sys.stdin).get('Threshold',500))")
```

#### 2. 타임아웃 공식
| 용도 | 공식 | 최솟값 |
|------|------|--------|
| 알람 ALARM 전환 대기 | `Period × EvalPeriods × 5` | **300초** |
| 복원 후 OK 전환 대기 | `Period × EvalPeriods × 3` | **180초** |
| 폴링 간격 | `min(Period, 15)` | 10초 |

#### 3. Retry 패턴 (핵심)
모든 외부 호출 (`kubectl port-forward`, `curl`, `aws` CLI)은 일시적 실패 가능. **retry 함수**를 정의하고 모든 호출에 적용:

```bash
retry() {
  local max_attempts=$1 delay=$2; shift 2
  local attempt=1
  while [ $attempt -le $max_attempts ]; do
    if "$@" 2>/dev/null; then return 0; fi
    echo "  retry $attempt/$max_attempts failed, waiting ${delay}s..."
    sleep $delay
    attempt=$((attempt + 1))
  done
  return 1
}
```

적용 대상:
- **kubectl port-forward + curl**: 연결 실패 시 3회 retry (port-forward 재시작 포함)
- **장애 주입/해제 API 호출**: 응답 검증 후 실패 시 3회 retry
- **알람 상태 폴링**: 이미 while 루프이므로 추가 retry 불필요
- **AWS CLI 호출**: 일시적 네트워크 오류 시 2회 retry

#### 4. 장애 재주입 (reinject)
알람 ALARM 대기 중 장애가 풀릴 수 있음. `REINJECT_INTERVAL = Period` 간격으로 장애를 재주입:

```bash
if [ $((ELAPSED % REINJECT_INTERVAL)) -eq 0 ] && [ $ELAPSED -gt 0 ]; then
  call_with_retry "/inject-latency?seconds=$LATENCY_SECONDS"
fi
```

#### 5. 장애 강도
알람 임계값의 **2배 이상** 주입. 임계값 근처 값은 통계 방식(p99 vs Average)에 따라 알람이 안 뜰 수 있음.

#### 6. 시뮬레이션 환경 최적화
이 스크립트는 실제 장애가 아니라 **시뮬레이션(카오스 엔지니어링 실습)**입니다. 알람이 확실하게 발동되도록 조건을 유리하게 만들어야 합니다:

- **장애 강도를 임계값의 3~5배로**: 임계값 500ms → 2~5초 지연 주입. 메트릭 집계(Average, p99 등)에 관계없이 확실히 초과
- **주입 후 워밍업 대기**: 장애 주입 직후 알람 폴링을 시작하지 말고, `sleep $PERIOD` (1 평가주기)를 먼저 대기. CloudWatch 메트릭 수집 파이프라인에 데이터가 도착할 시간을 확보
- **보조 트래픽 생성**: ApplicationSignals는 실제 요청이 있어야 레이턴시를 측정. worker가 이미 호출 중이지만, 장애 주입 후 `curl` 루프로 추가 요청을 보내서 고지연 데이터포인트를 더 빠르게 누적:
  ```bash
  # 보조 트래픽 (백그라운드, 알람 폴링과 병렬)
  for i in $(seq 1 20); do
    curl -s -X POST -d 'test' -m 60 "http://localhost:8080/" >/dev/null 2>&1 &
    sleep 3
  done
  ```
- **알람 INSUFFICIENT_DATA도 처리**: 메트릭 데이터 부족으로 INSUFFICIENT_DATA일 수 있음. 이 상태에서도 폴링을 계속하고, MAX_WAIT까지 기다려야 함

---

## 응답 형식

1. 인프라 파악 결과 간단 설명
2. `\`\`\`json` 코드블록 안에 시나리오 JSON
3. 사용자가 스크립트도 요청한 경우 (`#include-script`), JSON 다음에 `\`\`\`bash` 코드블록으로 실행 스크립트
