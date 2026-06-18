# 설계: Hasher X-Ray Fault Trace 버그 수정

## 최종 분석 결과 (확정)

### 결론: 코드/계측 문제 아님. CW Agent 내부 sampling 문제.

/error fault trace는 X-Ray에 **정상적으로 나타남**. `fault: true`, `http.status_code: 500`, `error.type: ValidationError` 모두 정상 기록됨.

이전에 "안 나타남"이라고 판단한 것은 **호출 횟수가 너무 적었기 때문**. CW Agent 내부 sampling이 ~2-5%로 적용되어, 1-5회 호출로는 X-Ray에 나타날 확률이 극히 낮았음.

### 검증 데이터

| 소스 | 엔드포인트 | 호출 횟수 | X-Ray 표시 | 비율 |
|------|-----------|----------|-----------|------|
| wget (pod 내부) | GET /error (500) | 130회 | 1개 | 0.8% |
| wget (pod 내부) | GET / (200) | 120회 | 6개 | 5% |
| kubelet probe | GET / (200) | ~60회/5분 | 1개 | ~1.7% |
| worker ADOT | POST / (200) | ~150회/5분 | 1개 | ~0.7% |

### X-Ray /error fault segment (실제 확인)

```json
{
  "name": "hasher",
  "fault": true,
  "http": {
    "request": { "url": "http://10.0.12.33:80/error", "method": "GET" },
    "response": { "status": 500 }
  },
  "annotations": {
    "aws.local.service": "hasher",
    "aws.local.operation": "GET /error"
  },
  "metadata": {
    "error.type": "ValidationError",
    "error.message": "Invalid input format",
    "http.status_code": 500
  }
}
```

### span 비교 (kubelet vs wget vs /error)

| 항목 | kubelet GET / | wget GET / | wget GET /error |
|------|--------------|------------|-----------------|
| SDK | opentelemetry for ruby | 동일 | 동일 |
| auto_instrumentation | false | 동일 | 동일 |
| instrumentation_scope | Rack 0.29.0 | 동일 | 동일 |
| process.pid | 1 | 1 | 1 |
| process.command | hasher.rb | 동일 | 동일 |
| span.kind | SERVER | 동일 | 동일 |
| parent_span_id | 0000 (root) | 동일 | 동일 |
| http.route | / | / | /error |
| http.status_code | 200 | 200 | 500 |
| http.user_agent | kube-probe/1.29+ | Wget | Wget |
| error.type | (없음) | (없음) | ValidationError |
| status.code | 1 (OK) | 1 (OK) | 2 (ERROR) |
| X-Ray fault | false | false | true |
| X-Ray 표시 | ✅ (sampling) | ✅ (sampling) | ✅ (sampling) |

**결론: 모든 span이 동일한 방식으로 생성/전송됨. 차이 없음. 모두 sampling에 의해 일부만 X-Ray에 표시.**

### 근본 원인: CW Agent 내부 sampling

CW Agent의 Application Signals pipeline 내부에서 X-Ray remote sampling이 적용됨.
우리의 custom sampling rule (FixedRate=1.0, Reservoir=100)이 CW Agent 내부 sampler에 적용되지 않고,
Default rule (reservoir=1/s, fixed_rate=5%)로 폴백하는 것으로 추정.

## 해결 방안

### 방안 1: CW Agent sampling 조정 (우선 시도)

hasher에 `OTEL_TRACES_SAMPLER=xray` + `OTEL_TRACES_SAMPLER_ARG=endpoint=http://cloudwatch-agent.amazon-cloudwatch:2000` 설정.
→ Ruby SDK가 xray sampler를 지원하지 않으므로 불가.

### 방안 2: 별도 OTel Collector 배포 (CW Agent 우회)

별도의 OTel Collector를 배포해서 hasher span을 X-Ray OTLP endpoint로 직접 전송.
→ 확실하지만 복잡.

### 방안 3: 테스트 시나리오에서 대량 호출 (현실적 해결)

DevOps Agent 테스트 시 /error, /slow를 충분히 많이 호출 (100회+)하면 sampling에 걸려서 X-Ray에 나타남.
test-scenarios.yaml의 시나리오가 반복 호출하도록 설정.
→ 코드 변경 없음, 가장 간단.

### 방안 4: Transaction Search 활용

CloudWatch Transaction Search는 Application Signals가 처리한 모든 span을 검색 가능.
X-Ray sampling과 무관하게 모든 trace를 볼 수 있음.
→ DevOps Agent가 Transaction Search API를 사용하면 sampling 문제 우회 가능.
