# DevOps Agent End-to-End 테스트 결과

> 각 시나리오의 전체 파이프라인 검증 결과를 기록합니다.
> 파이프라인: 장애 발생 → 로그/trace 기록 → Application Signals 메트릭 → CW 알람 → SNS → Lambda → DevOps Agent webhook

---

## 파이프라인 검증 상태 요약

| 단계 | 상태 | 비고 |
|------|------|------|
| X-Ray Trace Segment Destination | ✅ | `XRay` (ACTIVE) — 이전에 `CloudWatchLogs`로 변경되어 trace 안 보이는 문제 해결 |
| X-Ray Sampling Rule | ✅ | `FixedRate: 1.0`, `ReservoirSize: 100` (100% 샘플링) |
| ADOT Auto-Instrumentation | ✅ | hasher, rng, worker, webui 모두 init container 주입 확인 |
| Application Signals 메트릭 | ✅ | `eks:devops-agent-test-cluster/dockercoins` dimension으로 정상 기록 |
| CW Agent EMF Exporter | ✅ | PutLogEvents 시간순서 에러 해결 (CW Agent 재시작) |
| CloudWatch 알람 | ✅ | Error, Fault, Latency, Pod Restart 알람 모두 정상 동작 |
| SNS → Lambda 전달 | ✅ | `devops-agent-test-alarm-to-webhook` Lambda 정상 실행 |
| Lambda → DevOps Agent Webhook | ✅ | `Webhook 200` 응답 확인 |

---

## 시나리오별 검증 결과

### C07: 데이터 오염 연쇄 (End-to-End 완전 검증)

| 항목 | 결과 |
|------|------|
| 테스트 일시 | 2026-03-12 06:43 UTC |
| 트리거 | `kubectl set env deployment/rng RNG_CORRUPTION_RATE=0.5 -n dockercoins` |
| hasher 로그 | ✅ `ERROR: Validation failed: Empty input received from client` (약 50% 비율) |
| X-Ray trace | ✅ 282개 trace 중 45개 error trace (5분 범위) |
| X-Ray segment 상세 | ✅ `error=true`, `http.status=400`, `error.type=ValidationError`, `cause.exceptions[0].type=ValueError` |
| Application Signals Error 메트릭 | ✅ `Sum > 0` 확인 (dimension: `eks:devops-agent-test-cluster/dockercoins`) |
| hasher-errors 알람 | ✅ OK → ALARM 전환 (약 90초 소요) |
| dockercoins-unhealthy composite 알람 | ✅ ALARM 전환 |
| Lambda webhook 전달 | ✅ `Webhook 200 for devops-agent-test-hasher-errors` |
| Lambda webhook 전달 (composite) | ✅ `Webhook 200 for devops-agent-test-dockercoins-unhealthy` |
| DevOps Agent investigation | ⏳ Agent 콘솔에서 확인 필요 |


### A02: High Latency (격리 테스트)

| 항목 | 결과 |
|------|------|
| 테스트 일시 | 2026-03-10 (이전 세션) |
| 트리거 | curl pod → `GET http://hasher/slow?delay=5` (100회+) |
| X-Ray trace | ✅ 40개 latency trace 확인 (`responsetime > 4s`) |
| hasher-high-latency 알람 | ✅ ALARM 전환 |
| 한계 | 격리된 엔드포인트 호출 — 실제 서비스 체인 아님, 근본 원인 추적 불가 |

### A03: HTTP 500 Errors (격리 테스트)

| 항목 | 결과 |
|------|------|
| 테스트 일시 | 2026-03-10 (이전 세션) |
| 트리거 | curl pod → `GET http://hasher/error` (100회+) |
| X-Ray trace | ✅ 9개 fault trace (`fault=true`, `http.status=500`, `ValueError`) |
| Application Signals Fault 메트릭 | ✅ 기록됨 |
| 한계 | 격리된 엔드포인트 호출 — 실제 서비스 체인 아님 |

### C05: 서비스 의존성 장애 (hasher 다운)

| 항목 | 결과 |
|------|------|
| 테스트 일시 | 2026-03-10 (이전 세션) |
| 트리거 | `kubectl delete pod hasher-xxx -n dockercoins` |
| X-Ray trace | ✅ 2개 fault trace (`worker fault=true, ConnectionError`) |
| worker 로그 | ✅ `requests.exceptions.ConnectionError: HTTPConnectionPool(host='hasher')` |
| 한계 | hasher Pod 자동 복구 (Deployment) → 일시적 장애만 |

### C01: Redis 장애 전파

| 항목 | 결과 |
|------|------|
| 테스트 일시 | 2026-03-10 (이전 세션) |
| 트리거 | `kubectl delete pod redis-xxx -n dockercoins` |
| worker 로그 | ✅ `redis.exceptions.ConnectionError: Error 111 connecting to redis:6379` |
| X-Ray trace | ✅ worker fault trace 확인 |

---

## X-Ray Trace 유형별 검증 요약

| 시나리오 | Trace 유형 | X-Ray 필드 | 검증 |
|---------|-----------|-----------|------|
| C07 (데이터 오염 400) | error | `error=true, http=400, cause.exceptions[0].type=ValueError` | ✅ |
| C05 (hasher 다운) | fault | `fault=true, ConnectionError` | ✅ |
| A03 (HTTP 500 /error) | fault | `fault=true, http=500, ValueError` | ✅ |
| A02 (High Latency 5s) | latency | `http=200, responsetime>4s` | ✅ |
| C01 (Redis 다운) | fault | `fault=true, redis ConnectionError` | ✅ |

---

## 알려진 이슈 및 주의사항

### 1. Application Signals 메트릭 "0" 오진
- 에러가 없는 시간대에 Error 메트릭을 조회하면 0 또는 데이터포인트 없음
- 이는 정상 — 에러가 발생해야 메트릭이 기록됨
- `TreatMissingData: notBreaching` 설정으로 데이터 없는 구간은 OK 유지

### 2. Environment Dimension 불일치 (과거 이력)
- hasher Error 메트릭이 3가지 Environment dimension으로 존재:
  - `eks:devops-agent-test-cluster/dockercoins` (현재 활성 — ADOT auto-instrumentation)
  - `eks:default` (비활성 — 이전 Ruby 수동 계측)
  - `k8s:default` (비활성 — 더 이전 버전)
- 알람은 `eks:devops-agent-test-cluster/dockercoins`를 사용 중 ✅

### 3. CW Agent PutLogEvents 시간순서 에러
- 장시간 운영 후 `awsemfexporter` 경로에서 발생 가능
- 해결: `kubectl rollout restart daemonset/cloudwatch-agent -n amazon-cloudwatch`
- X-Ray trace는 별도 경로(`awsxrayexporter`)이므로 영향 없음

### 4. X-Ray Trace Segment Destination
- 계정/리전 레벨 설정 — `CloudWatchLogs`로 변경되면 API 조회 불가
- 확인: `aws xray get-trace-segment-destination`
- 수정: `aws xray update-trace-segment-destination --destination XRay`

---

## 미검증 시나리오 (End-to-End)

아래 시나리오들은 구현되어 있지만 전체 파이프라인(알람 → Lambda → DevOps Agent)까지의 end-to-end 검증은 미완료:

- A01 (OOMKilled) — pod-restarts 알람 경로
- A04 (CPU Spike) — cluster-high-cpu 알람 경로
- A05 (Process Crash) — pod-restarts 알람 경로
- C02 (네트워크 차단) — rng-errors 알람 경로
- C03 (노드 장애) — FIS 경로
- C04 (RDS 페일오버) — FIS 경로
- C06 (리소스 경쟁) — FIS + latency 알람 경로
- K01~K11 — K8s 레이어 시나리오들
- I01~I05 — AWS 인프라 시나리오들

### 누락된 시나리오
- 연쇄 지연 (Cascading Latency) — 환경변수 기반 hasher 지연 주입 → 서비스 체인 전체 latency 증가 → 알람 → Agent 근본 원인 분석
