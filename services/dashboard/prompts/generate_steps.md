## 절대 금지

- 시나리오 JSON의 command 필드를 그대로 복사/실행하지 마라 — intent 필드의 의도를 아래 ScenarioContext API로 구현
- subprocess, os.system, os.popen 사용 금지
- wget 사용 금지 (ctx.curl 또는 ctx.kubectl 전용)
- inject-latency는 반드시 2초 (seconds=2) — 5초 이상은 worker timeout으로 데이터포인트 미기록

---

위 시나리오를 실행하는 **Python steps.py**를 아래 표준 프레임워크에 맞춰 만들어줘.

## 프레임워크 API

```python
from scenario_runner import step, abort_condition, StepResult, ScenarioContext, StopScenario

# Action step (순차 실행, 기본값)
@step(number, "step 이름", max_retries=0, retry_delay=5.0)
def step_name(ctx: ScenarioContext) -> StepResult:
    # ctx 메서드로 작업 수행
    return StepResult("pass", "상세 내용")
    # 또는
    return StepResult("fail", "실패 이유", error_category="timeout")

# Observe step (폴링 루프에서 매 tick 호출)
@step(number, "조건 감시", step_type="observe", poll_interval=15, timeout=600)
def observe_condition(ctx: ScenarioContext):
    """None 반환 = 아직 미달성 (계속 폴링). StepResult 반환 = 판정 완료."""
    state = ctx.alarm_info("alarm-name").get("State", "UNKNOWN")
    if state == "ALARM":
        return StepResult("pass", "ALARM 전환 확인")
    return None  # 계속 대기

# Abort condition (observe step에 조기 중단 조건 등록)
@abort_condition(step_number=4)
def abort_if_hopeless(ctx: ScenarioContext):
    """None = 계속, StepResult("fail") = 즉시 중단."""
    return None
```

### ScenarioContext 메서드

| 메서드 | 설명 | 반환값 |
|--------|------|--------|
| `ctx.kubectl(args, timeout=30)` | kubectl -n {namespace} {args} 실행 | `(ok, stdout, stderr)` |
| `ctx.alarm_info(name="")` | CloudWatch 알람 정보 조회 | `dict` (Threshold, Period, EvalPeriods, State) |
| `ctx.compute_timeouts(info)` | 알람 설정 기반 타임아웃 계산 | `dict` (max_wait, ok_wait, poll_interval, reinject_interval) |
| `ctx.wait_alarm_state(target, timeout, poll_interval)` | 알람 상태 폴링 | `(ok, elapsed, final_state)` |
| `ctx.port_forward(service, local_port, remote_port=80)` | port-forward context manager | `PortForwardContext` |
| `ctx.curl(url, method="GET", data=None, timeout=10)` | HTTP 요청 | `(status_code, body)` |
| `ctx.inject_latency(service, seconds=2.0, port=18080)` | 안전 범위로 지연 주입 (0.5~3초 클램핑) | `(ok, body)` |
| `ctx.clear_latency(service, port=18080)` | 지연 해제 | `(ok, body)` |
| `ctx.send_auxiliary_traffic(pf, count=1)` | 보조 트래픽 전송 (데이터포인트 축적) | `None` |
| `ctx.fis_start(template_id, timeout=60)` | FIS 실험 시작 | `(ok, experiment_id)` |
| `ctx.fis_stop(experiment_id)` | FIS 실험 중지 | `bool` |
| `ctx.fis_status(experiment_id)` | FIS 실험 상태 조회 | `str` (running/completed/stopped/failed) |
| `ctx.get_or_create_pf(service, local_port, remote_port=80)` | 영속 port-forward (step 간 공유, cleanup까지 유지) | `PortForwardContext` |
| `ctx.run_pod(name, image, command)` | 임시 Pod 생성 | `bool` |
| `ctx.delete_pod(name)` | Pod 삭제 | `bool` |
| `ctx.wait_pod_running(name, timeout=60)` | Pod Running 대기 | `bool` |
| `ctx.log(message)` | 정보 로그 출력 (이벤트 스트림으로 전달) | `None` |
| `ctx._shared` | step 간 데이터 전달 dict | `dict` |

### StepResult

```python
StepResult(
    status="pass"|"fail"|"skip",
    detail="상세 설명",
    error_category=None,  # timeout | command_error | config_error | infra_missing | transient
)
```

### PortForwardContext

```python
with ctx.port_forward("hasher", 18080) as pf:
    code, body = ctx.curl(f"{{pf.url}}/inject-latency?seconds=5")
    pf.ensure_alive()  # 연결 끊기면 재연결
```

## Step 실행 모델: Action + Observe

Steps는 두 유형으로 나뉜다:

- **action** (기본값): 순차 실행. 한 번 실행 → pass/fail 판정.
- **observe**: 폴링 루프에서 매 tick마다 호출. `None` 반환 = 아직 미달성(계속), `StepResult` 반환 = 판정 완료.

실행 흐름: steps를 [action 블록, observe 블록, action 블록, observe 블록, ...] 단위로 분할하여 실행.
observe 블록 내 모든 step은 **단일 루프에서 동시 감시**. 어느 조건이 먼저 달성되든 즉시 판정.

```
action1 → action2 → action3  (순차)
       ↓
observe4 + observe5  (매 tick 동시 체크, 모두 pass될 때까지)
       ↓
action6  (순차)
       ↓
observe7  (매 tick 체크)
```

### @step 파라미터

| 파라미터 | action | observe | 설명 |
|----------|--------|---------|------|
| `step_type` | `"action"` (기본) | `"observe"` | step 유형 |
| `max_retries` | ✓ | - | action 재시도 횟수 |
| `poll_interval` | - | ✓ (기본 15) | 폴링 주기 (초) |
| `timeout` | - | ✓ (기본 600) | 최대 대기 (초, 0=compute_timeouts) |

### @abort_condition

observe step이 의미 없는 대기를 하는 것을 방지:

```python
@abort_condition(step_number=4)
def abort_alarm_wait(ctx):
    """FIS 실험이 끝났는데 알람 안 뜨면 더 이상 대기 무의미."""
    exp_id = ctx._shared.get("fis_experiment_id")
    if not exp_id:
        return None
    status = ctx.fis_status(exp_id)
    if status in ("completed", "stopped", "failed"):
        grace_start = ctx._shared.get("_abort_grace_start")
        if not grace_start:
            ctx._shared["_abort_grace_start"] = __import__("time").time()
            return None
        if __import__("time").time() - grace_start > 120:
            return StepResult("fail", "FIS 종료 후 2분 대기에도 미전환", error_category="timeout")
    return None
```

## 표준 Step 구조

1. **환경 사전 확인** (action) — 대상 서비스 Running 확인, 알람 정보 조회, 타임아웃 계산
2. **사전 정리** (action, 선택) — 이전 상태 초기화 (clear-latency, 이전 Pod 삭제 등)
3. **장애 주입** (action) — FIS 시작 또는 inject-latency 수행
4. **알람 ALARM 전환 감시** (observe) — 매 tick마다 알람 상태 확인 + abort 조건
5. **복원** (action) — FIS 중지 또는 clear-latency
6. **알람 OK 전환 감시** (observe) — 정상 복귀 확인

**핵심**: action에서 장애를 주입하고, observe에서 그 효과(알람 전환)를 감시. observe는 의미 없는 대기를 abort_condition으로 즉시 중단.

### 패턴 선택 기준

| 시나리오 유형 | 패턴 | 이유 |
|-------------|------|------|
| FIS 실험 기반 (CPU stress, network 등) | action/observe 분리 | FIS가 자체적으로 부하를 유지. observe는 알람만 감시 + abort_condition으로 FIS 종료 감지 |
| ApplicationSignals 지연 주입 (inject-latency) | action/observe 분리 + observe에서 재주입/트래픽 | observe에서 `ctx.get_or_create_pf()`로 영속 port-forward 사용, 매 tick 재주입+보조트래픽 |
| 단순 조건 확인 (Pod Ready, 서비스 연결) | action 단일 step 내 루프 | 빠른 수렴, abort 불필요 |

### observe step에서 port-forward + 보조 트래픽 패턴

```python
@step(3, "지연 주입", step_type="action")
def inject(ctx):
    pf = ctx.get_or_create_pf("hasher", 18080)  # 영속 — cleanup()까지 유지
    code, body = ctx.curl(f"{pf.url}/inject-latency?seconds=2")
    if not code or code >= 400:
        return StepResult("fail", f"주입 실패: {code}", error_category="command_error")
    ctx._shared["_last_inject"] = __import__("time").time()
    return StepResult("pass", f"지연 주입 완료: {body}")

@step(4, "알람 ALARM 감시", step_type="observe", poll_interval=15, timeout=600)
def observe_alarm(ctx):
    import time as _t
    pf = ctx.get_or_create_pf("hasher", 18080)  # 이미 있으면 재사용
    pf.ensure_alive()
    # 매 tick 보조 트래픽 (데이터포인트 누적)
    ctx.curl(f"{pf.url}/", method="POST", data="aux", timeout=10)
    # 재주입 (60초마다)
    last = ctx._shared.get("_last_inject", 0)
    if _t.time() - last >= 60:
        ctx.curl(f"{pf.url}/inject-latency?seconds=2")
        ctx._shared["_last_inject"] = _t.time()
    # 조건 체크
    alarm_name = ctx._shared.get("alarm_name", ctx.alarm_name)
    info = ctx.alarm_info(alarm_name)
    if info.get("State") == "ALARM":
        return StepResult("pass", "ALARM 전환 확인")
    return None
```

## 안정성 패턴 (반드시 준수)

CloudWatch 메트릭은 수집→집계→평가까지 1~3분 지연이 있다. 단순히 한번 주입하고 기다리면 알람이 안 뜬다.

### 1. 장애 주입 + 알람 대기를 하나의 step에서 수행 (레거시 패턴, 하위호환)

```python
@step(3, "장애 주입 및 알람 ALARM 대기", max_retries=1, retry_delay=10)
def inject_and_wait_alarm(ctx):
    alarm_name, info, timeouts = _ensure_alarm_context(ctx)

    with ctx.port_forward("hasher", 18080) as pf:
        # 최초 주입 (반드시 2초 — 5초 이상은 worker 타임아웃 유발)
        code, body = ctx.curl(f"{{pf.url}}/inject-latency?seconds=2")
        if not code or code >= 400:
            return StepResult("fail", f"주입 실패: {{code}}", error_category="command_error")
        ctx.log(f"지연 주입 완료: {{body}}")

        # 폴링하면서 reinject_interval마다 재주입 + 보조 트래픽
        max_wait = timeouts["max_wait"]
        poll_interval = timeouts["poll_interval"]
        reinject_interval = timeouts.get("reinject_interval", 60)
        elapsed = 0
        last_inject = 0

        while elapsed < max_wait:
            # 재주입 (장애가 풀릴 수 있으므로)
            if elapsed - last_inject >= reinject_interval and elapsed > 0:
                pf.ensure_alive()
                ctx.curl(f"{{pf.url}}/inject-latency?seconds=2")
                last_inject = elapsed
                ctx.log(f"재주입 ({{elapsed}}s)")

            # 보조 트래픽 (매 poll마다 요청하여 고지연 데이터포인트 누적)
            pf.ensure_alive()
            ctx.curl(f"{{pf.url}}/", method="POST", data="aux", timeout=10)

            import time; time.sleep(poll_interval)
            elapsed += poll_interval

            info = ctx.alarm_info(alarm_name)
            state = info.get("State", "UNKNOWN")
            ctx.log(f"[{{elapsed}}s/{{max_wait}}s] 알람: {{state}}")

            if state == "ALARM":
                return StepResult("pass", f"{{elapsed}}초 만에 ALARM 전환")

    return StepResult("fail", f"{{max_wait}}초 대기 후에도 ALARM 미전환 ({{state}})", error_category="timeout")
```

### 2. 장애 강도

**반드시 2초 지연을 주입**하라 (`inject-latency?seconds=2`). 이유:
- 임계값 500ms의 4배로 충분히 높다.
- 5초 이상 주입하면 worker가 ConnectTimeout으로 요청 자체를 포기하여, ApplicationSignals에 레이턴시 데이터포인트가 **전혀 기록되지 않는다**. 데이터포인트가 없으면 알람은 ALARM으로 전환될 수 없다.
- 2초 지연은 worker 요청이 "느리지만 성공"하므로 ApplicationSignals가 고지연 데이터포인트를 정상 기록한다.

### 3. 보조 트래픽 (필수)

ApplicationSignals는 실제 **성공한** 요청이 있어야 레이턴시를 측정한다. worker가 호출 중이지만 지연이 너무 크면 타임아웃으로 실패하여 메트릭이 쌓이지 않는다.

**반드시** port-forward를 통해 직접 서비스에 요청을 보내라. poll 루프 안에서 매 poll마다 1회 이상 요청을 보내야 데이터포인트가 누적된다:
```python
# poll 루프 내부에서
pf.ensure_alive()
ctx.curl(f"{pf.url}/", method="POST", data="auxiliary-traffic", timeout=10)
```

### 4. FIS 기반 장애 주입 패턴 (action + observe 모델)

FIS 시나리오는 action/observe 모델을 사용:

```python
import time
from scenario_runner import step, abort_condition, StepResult, ScenarioContext

def _ensure_alarm_context(ctx):
    alarm_name = ctx._shared.get("alarm_name") or ctx.alarm_name
    ctx._shared["alarm_name"] = alarm_name
    info = ctx._shared.get("alarm_info") or ctx.alarm_info(alarm_name)
    ctx._shared["alarm_info"] = info
    timeouts = ctx._shared.get("timeouts") or ctx.compute_timeouts(info)
    ctx._shared["timeouts"] = timeouts
    return alarm_name, info, timeouts

@step(3, "FIS 장애 주입", step_type="action", max_retries=1, retry_delay=10)
def fis_inject(ctx):
    alarm_name, info, timeouts = _ensure_alarm_context(ctx)
    template_id = ctx._shared.get("fis_template_id", "EXT...")

    ok, experiment_id = ctx.fis_start(template_id)
    if not ok:
        return StepResult("fail", f"FIS 시작 실패: {experiment_id}", error_category="command_error")
    ctx._shared["fis_experiment_id"] = experiment_id
    ctx.log(f"FIS 실험 시작: {experiment_id}")
    return StepResult("pass", f"FIS 실험 시작됨: {experiment_id}")

@step(4, "알람 ALARM 전환 감시", step_type="observe", poll_interval=15, timeout=600)
def observe_alarm_to_alarm(ctx):
    """매 tick 호출. ALARM이면 pass, 아니면 None."""
    alarm_name = ctx._shared.get("alarm_name", ctx.alarm_name)
    info = ctx.alarm_info(alarm_name)
    state = info.get("State", "UNKNOWN")
    if state == "ALARM":
        return StepResult("pass", f"알람 ALARM 전환 확인")
    return None

@abort_condition(step_number=4)
def abort_fis_ended(ctx):
    """FIS 종료 후 2분 대기에도 ALARM 미전환 → 즉시 fail."""
    exp_id = ctx._shared.get("fis_experiment_id")
    if not exp_id:
        return None
    status = ctx.fis_status(exp_id)
    if status in ("completed", "stopped", "failed"):
        grace_start = ctx._shared.get("_fis_grace_start")
        if not grace_start:
            ctx._shared["_fis_grace_start"] = time.time()
            return None
        if time.time() - grace_start > 120:
            return StepResult("fail", "FIS 종료 후 2분 대기에도 ALARM 미전환", error_category="timeout")
    return None

@step(5, "FIS 중지 + 복원", step_type="action")
def fis_restore(ctx):
    exp_id = ctx._shared.get("fis_experiment_id")
    if exp_id:
        ctx.fis_stop(exp_id)
    return StepResult("pass", "FIS 중지 완료")

@step(6, "알람 OK 전환 감시", step_type="observe", poll_interval=15, timeout=300)
def observe_alarm_to_ok(ctx):
    alarm_name = ctx._shared.get("alarm_name", ctx.alarm_name)
    info = ctx.alarm_info(alarm_name)
    state = info.get("State", "UNKNOWN")
    if state == "OK":
        return StepResult("pass", "알람 OK 복귀 확인")
    return None
```

**핵심 규칙**:
- FIS 시작 = action step, 알람 감시 = observe step (폴링 루프가 자동으로 매 tick 호출)
- abort_condition으로 FIS 종료 후 의미 없는 대기를 즉시 중단
- observe step 함수는 **한 번 호출 = 한 번 체크** (내부 루프 금지)

### 5. INSUFFICIENT_DATA 처리

메트릭 데이터 부족으로 `INSUFFICIENT_DATA`가 올 수 있다. 이 상태에서도 폴링을 계속해야 한다 (ALARM이 아닌 모든 상태는 계속 대기).

### 5. Pod 생성 시 AlreadyExists 처리 (필수)

`kubectl run`으로 pod를 생성할 때 반드시 AlreadyExists 에러를 처리하라:
```python
ok, stdout, stderr = ctx.kubectl("run my-pod --image=... --restart=Never ...")
if not ok:
    if "AlreadyExists" in stderr or "already exists" in stderr:
        ctx.kubectl("delete pod my-pod --ignore-not-found=true --wait=true", timeout=60)
        import time; time.sleep(5)
        ok, stdout, stderr = ctx.kubectl("run my-pod --image=... --restart=Never ...")
        if not ok:
            return StepResult("fail", f"Pod 재생성 실패: {stderr}", error_category="command_error")
    else:
        return StepResult("fail", f"Pod 생성 실패: {stderr}", error_category="command_error")
```

또한 사전 정리(step 2)에서 pod 삭제 시 `--wait=true`를 사용하여 완전 종료를 보장하라.

### 6. Pod Running 대기 timeout (필수)

EKS 클러스터에서 pod 수가 많으면 스케줄링에 60초 이상 소요될 수 있다 (`Too many pods` 이벤트).
`wait_pod_running()` 호출 시 반드시 **timeout=120** 이상을 사용하라:
```python
running = ctx.wait_pod_running("my-pod", timeout=120)
```

---

## 규칙

1. **언어는 반드시 Python** (bash 명령은 ctx 메서드를 통해서만)
2. **`@step` 데코레이터**로 각 step 등록 (number는 1부터 순서대로)
3. 타임아웃은 하드코딩 금지 → `ctx.compute_timeouts(alarm_info)` 사용
4. step 간 데이터 전달은 `ctx._shared` dict 사용
5. **ApplicationSignals alarm + 지연 주입 시 반드시 application-level 방식**:
   - `ctx.port_forward("hasher", 18080)` + `ctx.curl(f"{{pf.url}}/inject-latency?seconds=2")`
   - **반드시 2초 사용**. 5초 이상은 worker 타임아웃 → 데이터포인트 미기록 → 알람 미전환
   - FIS `pod-network-latency`는 ApplicationSignals에 반영 안됨
   - 복원: `ctx.curl(f"{{pf.url}}/clear-latency")`
   - poll 루프에서 매회 `ctx.curl(f"{{pf.url}}/", method="POST", data="aux", timeout=10)` 보조 트래픽 필수
6. 실패 시 `error_category` 설정 권장 (자동 분류도 가능하지만 명시적이 더 정확)
7. retry가 의미있는 step에만 `max_retries` 설정 (e.g., 네트워크 일시 오류 가능한 장애 주입)
8. `StopScenario` exception은 전체 시나리오 중단이 필요할 때만 사용
9. **resume 안전성**: 모든 step은 독립 실행 가능해야 한다. `ctx._shared`에서 값을 꺼낼 때 반드시 `.get()`으로 확인하고, 없으면 해당 step에서 직접 조회하라. `resume_from`으로 중간 step부터 재시작하면 이전 step이 skip되어 `_shared`가 비어있다.
   ```python
   # BAD — resume 시 KeyError 발생:
   timeouts = ctx._shared["timeouts"]
   alarm_name = ctx._shared["alarm_name"]

   # GOOD — 헬퍼 함수로 추출하여 모든 step에서 재사용:
   def _ensure_alarm_context(ctx):
       """alarm_name, alarm_info, timeouts를 _shared에 로드 (없으면 조회)."""
       alarm_name = ctx._shared.get("alarm_name")
       if not alarm_name:
           alarm_name = ctx.alarm_name or "시나리오JSON의 verification.alarms[0].name 값"
           ctx._shared["alarm_name"] = alarm_name
       info = ctx._shared.get("alarm_info")
       if not info:
           info = ctx.alarm_info(alarm_name)
           ctx._shared["alarm_info"] = info
       timeouts = ctx._shared.get("timeouts")
       if not timeouts:
           timeouts = ctx.compute_timeouts(info)
           ctx._shared["timeouts"] = timeouts
       return alarm_name, info, timeouts
   ```
10. **public API만 사용**: `ctx.log()`, `ctx.kubectl()`, `ctx.curl()` 등 위 테이블에 명시된 메서드만 호출. `ctx._log()`, `ctx._internal()` 등 underscore 시작 private 메서드를 호출하지 마라 (`ctx._shared`는 예외).

## 참고: 알람 이름

- 알람 이름은 시나리오 JSON의 `verification.alarms[].name`에 정의됨
- `ctx.alarm_name`에 첫 번째 알람이 자동 설정됨
- 여러 알람이 필요하면 `ctx.alarm_info("specific-alarm-name")` 사용

## 검증된 리소스 사용 규칙

앱이 아래에 "검증된 인프라 리소스" 테이블을 첨부합니다.
- **"사용 가능 ✓"** 표시된 알람/서비스만 steps.py에서 참조하세요
- **"사용 불가 ✗"** 리소스를 참조하면 dry-run에서 실패합니다
- 알람 이름은 테이블에 표시된 정확한 이름을 사용하세요 (오타나 추정 금지)
- datapoint가 0인 알람은 OTEL 미계측이므로 사용할 수 없습니다
- 생성된 코드는 자동으로 `--dry-run` 검증을 거칩니다. 검증 실패 시 에러와 함께 수정 요청이 옵니다

python 코드 블록만 출력. 설명 불필요.
