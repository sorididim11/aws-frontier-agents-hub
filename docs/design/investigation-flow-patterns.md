# DevOps Agent 조사 흐름 패턴

> 실제 조사 데이터(`fixtures/sample_journal_raw.json`)와 17개 시나리오 정의에서 추출한 패턴 분석

---

## 1. 조사 레코드 시퀀스 (5단계)

DevOps Agent가 조사 중 생성하는 journal record의 고정 순서:

```
symptom → message → observation(N개) → finding(N개) → investigation_summary
```

| 단계 | recordType | 역할 | 예시 |
|------|-----------|------|------|
| 1 | `symptom` | CW 알람 수신, 초기 증상 기록 | "Hasher 에러율 임계값 초과 알람 발생" |
| 2 | `message` | Agent의 분석 계획/진행 선언 | "Hasher 서비스의 에러 메트릭을 분석하겠습니다" |
| 3 | `observation` | 데이터 소스별 관찰 결과 (2-3개) | 메트릭 급감 패턴, X-Ray 400 에러, K8s 환경변수 변경 |
| 4 | `finding` | 분석 결론 (`root_cause` / `impact`) | "RNG corruption이 근본 원인", "Hasher 400은 영향" |
| 5 | `investigation_summary` | 종합 요약 + 미해결 gap | symptoms + findings + investigation_gaps |

### Observation → Finding 연결 구조

```
observation(hasher-error_pattern_analysis) ──┐
                                              ├──▶ finding(rng-corruption-active) [root_cause]
observation(k8s-rng_env_var_change) ─────────┘

observation(hasher-validation_error_400) ────┐
                                              ├──▶ finding(hasher-persistent-errors) [impact]
observation(hasher-error_pattern_analysis) ──┘
```

- 하나의 observation이 여러 finding에 참조될 수 있음
- finding의 `supporting_observations[]`로 역추적 가능

---

## 2. Signal 타입 (5종)

Agent가 observation 내에서 수집하는 데이터 소스:

| Signal Type | 데이터 소스 | 핵심 필드 | 용도 |
|------------|-----------|----------|------|
| `metric` | CloudWatch | `metricDataset[].data[]` (시계열 x,y) | 에러율/지연 추이 분석 |
| `trace` | X-Ray | `records[].spans[]` (서비스 호출 체인) | 서비스 간 호출 에러 추적 |
| `log` | CloudWatch Logs | `messages[]` (타임스탬프+로그라인) | 에러 메시지 상세 확인 |
| `code_snippet` | CodeGuru/GitHub | `code_diffs[]` (파일 경로+변경분) | 코드 변경 원인 분석 |
| `change_event` | K8s API | `resource`, `event_type`, `timestamp` | 배포/설정 변경 감지 |

### Signal 활용 빈도 (카테고리별)

```
Application(A):  metric ████  log ████  trace ███  code_snippet ████  change_event ██
Composite(C):    metric ████  log ███   trace ████ code_snippet ██    change_event ███
Infra(I):        metric ██    log ███   trace ██   code_snippet █     change_event ████
Kubernetes(K):   metric ██    log ████  trace █    code_snippet █     change_event ████
```

---

## 3. 시나리오 카테고리별 조사 패턴 (4종)

### A — Application (단일 서비스 내부 문제)

**시나리오**: A01-oom, A02-latency, A03-error, A04-cpu, A05-crash

```
CW 알람 (해당 서비스)
  → 서비스 메트릭 분석 (에러율/지연/CPU/메모리)
    → 로그 확인 (에러 메시지 패턴)
      → 코드 분석 (버그 위치 특정)
        → root cause: 코드 버그
```

**특징**:
- 장애 서비스 = 원인 서비스 (단일 hop)
- **코드 분석(`code_snippet`)이 결정적** — sleep 변경, 버퍼 무한증가 등
- evaluation에서 `code_analysis` 가중치 25%

**예시 (A01-oom)**:
> 캐시 모드 aggressive → 요청당 256KB 버퍼 추가(eviction 없음) → 128Mi 초과 → OOMKilled

---

### C — Composite (다중 서비스 연쇄 장애)

**시나리오**: C01-redis, C05-service, C07-corrupted-data

```
CW 알람 (서비스 B — 표면 증상)
  → B 메트릭/트레이스 분석
    → upstream 서비스 A 호출 패턴 추적
      → A의 상태/변경 확인
        → root cause: 서비스 A (≠ 서비스 B)
```

**특징**:
- **표면 증상 ≠ 근본 원인** — 알람이 울린 서비스가 범인이 아님
- **X-Ray 트레이스가 핵심** — 서비스 간 호출 체인에서 진짜 원인 추적
- evaluation에서 `upstream_tracing` 가중치 25%, `false_leads` 10%

**예시 (C07-corrupted-data)**:
> Hasher 400 에러 알람 → 트레이스: worker→hasher 에러 → upstream: RNG가 빈 데이터 반환 → root cause: RNG corruption (NOT hasher 버그)

---

### I — Infrastructure (AWS 인프라 레벨)

**시나리오**: I02-sg-block, I07-fis-node

```
앱 에러 (연결 실패/타임아웃)
  → 네트워크/노드 레벨 확인
    → AWS 리소스 변경 이력 추적
      → root cause: SG 규칙 제거 / 노드 장애
```

**특징**:
- 앱 레벨 에러의 원인이 인프라에 있음
- **CloudTrail / VPC Flow Logs / EC2 이벤트**가 결정적
- K8s 위에서 실행되지만 원인은 AWS 레이어

**예시 (I02-sg-block)**:
> DB 연결 타임아웃 → SG 인바운드 규칙 제거됨 → PostgreSQL 5432 포트 차단

---

### K — Kubernetes (K8s 오브젝트 레벨)

**시나리오**: K01-imagepull, K02-crashloop, K05-configmap, K07-networkpolicy, K10-liveness

```
Pod 상태 이상 감지
  → K8s 이벤트 / describe 확인
    → 오브젝트 설정 분석
      → root cause: K8s 설정 문제
```

**특징**:
- **K8s API 데이터가 결정적** — Pod status, Events, Describe 출력
- CloudWatch/X-Ray보다 `kubectl` 계열 정보가 중요
- 비교적 단순한 1-hop 분석 (설정 → 증상)

**예시 (K02-crashloop)**:
> Pod CrashLoopBackOff → 컨테이너 로그: "ERROR: Missing required configuration" → exit 1

---

## 4. 공통 조사 흐름 (Hypothesis-Driven Investigation)

```
┌──────────────────────────────────────────────────────────────┐
│  [Trigger]                                                    │
│  CW Alarm → SNS → Lambda → DevOps Agent webhook              │
└──────────────┬───────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────┐
│  [Phase 1: Symptom]                                           │
│  알람 내용 파싱, 영향 범위 초기 판단                              │
│  → symptom record 생성                                        │
└──────────────┬───────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────┐
│  [Phase 2: Observation — 가설 기반 데이터 수집]                  │
│                                                               │
│  가설 1: 해당 서비스 자체 문제                                    │
│    → 메트릭(metric) + 로그(log) 확인                            │
│                                                               │
│  가설 2: upstream 서비스 문제                                    │
│    → 트레이스(trace) + upstream 메트릭 확인                      │
│                                                               │
│  가설 3: 설정/배포 변경 문제                                      │
│    → K8s 변경이벤트(change_event) + 코드 diff(code_snippet)     │
│                                                               │
│  → observation record N개 생성                                 │
└──────────────┬───────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────┐
│  [Phase 3: Finding — 가설 검증 결과]                             │
│                                                               │
│  finding(root_cause): 근본 원인 1개                             │
│  finding(impact): 영향/결과 N개                                 │
│                                                               │
│  각 finding은 supporting_observations[]로 근거 명시             │
└──────────────┬───────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────┐
│  [Phase 4: Summary]                                           │
│  symptoms + findings 종합                                     │
│  investigation_gaps: 미해결 질문 목록                            │
└──────────────────────────────────────────────────────────────┘
```

---

## 5. 평가 체계 (Evaluation Rubric)

### 공통 5축 평가

| 축 | 가중치 | 설명 |
|----|--------|------|
| `root_cause_match` | 30% | 근본 원인을 정확히 식별했는가 |
| `code_analysis` 또는 `upstream_tracing` | 25% | 카테고리에 따라: 코드 분석(A) vs upstream 추적(C) |
| `data_sources` | 20% | 관련 데이터 소스를 충분히 활용했는가 |
| `causal_chain` | 15% | 원인→결과 인과관계를 논리적으로 설명했는가 |
| `false_leads` | 10% | 잘못된 방향에 시간을 낭비하지 않았는가 |

### 카테고리별 Required 데이터 소스

| 카테고리 | 필수 소스 |
|----------|----------|
| A (Application) | K8s, CloudWatch, Code |
| C (Composite) | Traces, Logs, K8s |
| I (Infrastructure) | CloudTrail, VPC Flow, EC2 Events |
| K (Kubernetes) | K8s Events, Pod Status, Describe |

---

## 6. 시나리오 전체 목록

| ID | 이름 | 카테고리 | 레이어 | 핵심 패턴 |
|----|------|---------|--------|----------|
| A01 | Memory Leak → OOMKilled | single-service | App → Resource | 코드 버그 (버퍼 무한증가) |
| A02 | Cascading Latency | single-service | App → Performance | 코드 변경 (sleep 2s) |
| A03 | Error Injection | single-service | App → Error | 에러 주입 |
| A04 | CPU Spike | single-service | App → Resource | CPU 과부하 |
| A05 | Process Crash | single-service | App → Stability | 프로세스 크래시 |
| C01 | Redis Pod Failure | multi-service | Composite → Cache | 캐시 서버 장애 → 전체 영향 |
| C05 | Service Dependency | multi-service | Composite → Dependency | 의존 서비스 다운 → 연쇄 에러 |
| C07 | RNG Data Corruption | multi-service | Composite → Data | 데이터 오염 → downstream 검증 실패 |
| I02 | Security Group Block | aws | AWS → Network | SG 규칙 제거 → DB 연결 차단 |
| I07 | FIS Node Failure | aws | AWS → Compute | 노드 장애 주입 |
| K01 | ImagePullBackOff | kubernetes | K8s → Image | 이미지 풀 실패 |
| K02 | CrashLoopBackOff | kubernetes | K8s → Container | 컨테이너 즉시 종료 |
| K05 | ConfigMap Error | kubernetes | K8s → Config | 설정 오류 |
| K07 | NetworkPolicy Deny | kubernetes | K8s → Network | 네트워크 정책 차단 |
| K10 | Liveness Probe Fail | kubernetes | K8s → Health | 헬스체크 실패 |
