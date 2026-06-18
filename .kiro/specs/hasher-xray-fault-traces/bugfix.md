# Bugfix 요구사항 문서

## 소개

DockerCoins 애플리케이션의 실제 서비스 호출 체인(worker → hasher POST /)에서 에러가 발생할 때, 해당 fault trace가 AWS X-Ray에 표시되는지 검증하고, DevOps Agent가 이를 발견할 수 있도록 보장합니다.

### 배경

**이전 분석의 잘못된 전제:**
- hasher `/error` 엔드포인트의 fault trace가 X-Ray에 안 나타난다고 판단
- 실제로는 CW Agent 내부 sampling (~2-5%) 때문에 소수 호출(1-5회) 시 안 보였던 것
- 대량 호출(130회) 시 `/error` fault trace는 정상 표시됨 확인

**근본 문제:**
- `/error` 엔드포인트는 실제 서비스 흐름에 포함되지 않음
- curl pod 호출은 trace context가 없어서 X-Ray에 독립적으로 나타남
- 실제 비즈니스 로직(worker → hasher POST /)에서 에러 발생 시 fault trace 검증 필요

**DockerCoins 실제 서비스 흐름:**
```
worker → rng GET /32 (random bytes 요청)
  ↓
worker ← rng (random bytes 응답)
  ↓
worker → hasher POST / (hash 계산 요청)
  ↓
worker ← hasher (hash 응답)
  ↓
worker → redis (결과 저장)
```

**현재 상태:**
- worker → hasher POST / 호출은 X-Ray에 정상 trace 표시됨
- worker는 ADOT auto-instrumentation으로 trace context 전파함
- hasher는 수동 OpenTelemetry SDK로 trace 수신/생성함

## 버그 분석

### 현재 동작 (결함)

1.1 WHEN worker가 hasher POST /에 정상 데이터를 전송할 때 THEN X-Ray에 정상 trace가 표시되지만, 에러 발생 시 fault trace 표시 여부는 검증되지 않음

1.2 WHEN hasher POST / 엔드포인트에서 의도적으로 에러를 발생시킬 방법이 없을 때 THEN 실제 서비스 호출 체인에서 fault trace를 테스트할 수 없음

1.3 WHEN hasher가 잘못된 입력을 받았을 때 THEN 현재는 에러 처리 로직이 없어서 예외가 발생하거나 잘못된 결과를 반환할 수 있음

1.4 WHEN DevOps Agent가 fault trace를 찾으려 할 때 THEN 실제 서비스 호출 체인에서 발생한 에러가 없어서 테스트 시나리오가 불완전함

### 기대 동작 (정상)

2.1 WHEN worker가 hasher POST /에 정상 데이터를 전송할 때 THEN X-Ray에 정상 trace가 표시되어야 함 (SHALL)

2.2 WHEN hasher POST /가 잘못된 입력을 받았을 때 (예: 빈 문자열, 너무 긴 입력) THEN HTTP 400 또는 500을 반환하고 X-Ray에 fault trace가 표시되어야 함 (SHALL)

2.3 WHEN hasher POST /에서 hash 계산 중 의도적 에러가 발생할 때 (예: 10% 확률) THEN HTTP 500을 반환하고 X-Ray에 fault trace가 표시되어야 함 (SHALL)

2.4 WHEN worker → hasher 호출 체인에서 에러가 발생할 때 THEN X-Ray trace에 fault=true, http.status_code=4xx/5xx, error.type/message가 기록되어야 함 (SHALL)

2.5 WHEN DevOps Agent가 fault trace를 검색할 때 THEN worker → hasher 호출 체인의 fault trace를 발견할 수 있어야 함 (SHALL)

### 변경 없는 동작 (회귀 방지)

3.1 WHEN worker가 hasher POST /에 정상 데이터를 전송할 때 THEN 시스템은 계속해서 올바른 SHA256 해시를 반환하고 정상 trace를 X-Ray에 표시해야 함 (SHALL CONTINUE TO)

3.2 WHEN `GET /` 엔드포인트가 호출될 때 THEN 시스템은 계속해서 정상 트레이스를 X-Ray에 표시해야 함 (SHALL CONTINUE TO)

3.3 WHEN `GET /slow` 엔드포인트가 호출될 때 THEN 시스템은 계속해서 지연 시간이 포함된 트레이스를 X-Ray에 표시해야 함 (SHALL CONTINUE TO)

3.4 WHEN `GET /crash` 엔드포인트가 호출될 때 THEN 시스템은 계속해서 프로세스를 종료하고 pod restart를 트리거해야 함 (SHALL CONTINUE TO)

3.5 WHEN hasher 서비스가 Kubernetes에서 실행될 때 THEN 시스템은 계속해서 liveness/readiness probe에 정상 응답해야 함 (SHALL CONTINUE TO)

3.6 WHEN worker가 정상적으로 동작할 때 THEN 시스템은 계속해서 초당 ~3-5개의 해시를 생성하고 redis에 저장해야 함 (SHALL CONTINUE TO)


## 제안된 테스트 시나리오

### 옵션 1: 입력 검증 실패 (Input Validation Error)

hasher POST /에 입력 검증 로직을 추가하여, 잘못된 입력 시 에러를 반환:

```ruby
post '/' do
  body = request.body.read
  
  # 입력 검증
  if body.empty?
    span = OpenTelemetry::Trace.current_span
    span.set_attribute('error.type', 'ValidationError')
    span.set_attribute('error.message', 'Empty input not allowed')
    span.status = OpenTelemetry::Trace::Status.error('Validation failed')
    
    status 400
    return "ERROR: Empty input\n"
  end
  
  if body.length > 1024 * 1024  # 1MB limit
    span = OpenTelemetry::Trace.current_span
    span.set_attribute('error.type', 'ValidationError')
    span.set_attribute('error.message', 'Input too large')
    span.status = OpenTelemetry::Trace::Status.error('Validation failed')
    
    status 400
    return "ERROR: Input too large\n"
  end
  
  # 정상 처리
  sleep 2
  content_type 'text/plain'
  "#{Digest::SHA2.new().update(body)}"
end
```

**트리거 방법:**
```bash
# worker pod에서 실행
curl -X POST http://hasher/ -d ""  # 빈 입력
curl -X POST http://hasher/ -d "$(head -c 2M /dev/urandom | base64)"  # 너무 큰 입력
```

**기대 결과:**
- X-Ray에 worker → hasher trace 표시
- fault=true, http.status_code=400
- error.type=ValidationError

### 옵션 2: 확률적 에러 주입 (Probabilistic Error Injection)

hasher POST /에서 10% 확률로 hash 계산 중 에러 발생:

```ruby
post '/' do
  body = request.body.read
  
  # 10% 확률로 에러 발생
  if rand < 0.1
    span = OpenTelemetry::Trace.current_span
    span.set_attribute('error.type', 'ComputationError')
    span.set_attribute('error.message', 'Hash computation failed')
    span.status = OpenTelemetry::Trace::Status.error('Computation failed')
    
    status 500
    return "ERROR: Hash computation failed\n"
  end
  
  # 정상 처리
  sleep 2
  content_type 'text/plain'
  "#{Digest::SHA2.new().update(body)}"
end
```

**트리거 방법:**
```bash
# worker가 자동으로 호출하므로 별도 트리거 불필요
# 또는 대량 호출로 에러 발생 보장:
for i in {1..100}; do curl -X POST http://hasher/ -d "test$i"; done
```

**기대 결과:**
- X-Ray에 worker → hasher trace 표시 (일부 fault)
- fault=true, http.status_code=500
- error.type=ComputationError

### 옵션 3: 환경변수 기반 에러 모드 (Environment-based Error Mode)

환경변수로 에러 모드를 활성화:

```ruby
post '/' do
  body = request.body.read
  
  # 에러 모드 활성화 시
  if ENV['HASHER_ERROR_MODE'] == 'true'
    span = OpenTelemetry::Trace.current_span
    span.set_attribute('error.type', 'ErrorModeEnabled')
    span.set_attribute('error.message', 'Service in error mode')
    span.status = OpenTelemetry::Trace::Status.error('Error mode')
    
    status 500
    return "ERROR: Service in error mode\n"
  end
  
  # 정상 처리
  sleep 2
  content_type 'text/plain'
  "#{Digest::SHA2.new().update(body)}"
end
```

**트리거 방법:**
```bash
# Deployment에 환경변수 추가
kubectl set env deployment/hasher HASHER_ERROR_MODE=true

# 복원
kubectl set env deployment/hasher HASHER_ERROR_MODE-
```

**기대 결과:**
- X-Ray에 worker → hasher trace 표시 (모두 fault)
- fault=true, http.status_code=500
- error.type=ErrorModeEnabled

## 권장 구현

**전체 서비스 체인 기반 에러 시나리오:**

### 1단계: rng에서 잘못된 데이터 생성

rng에 환경변수로 데이터 오염 모드 추가:

```python
# rng.py
@app.route("/<int:how_many_bytes>")
def rng(how_many_bytes):
    time.sleep(0.1)
    
    # 환경변수로 데이터 오염 확률 제어 (기본 0%)
    corruption_rate = float(os.environ.get('RNG_CORRUPTION_RATE', '0.0'))
    
    if random.random() < corruption_rate:
        # 잘못된 데이터 반환 (빈 데이터 또는 너무 짧은 데이터)
        return Response(
            b'',  # 빈 데이터
            content_type="application/octet-stream"
        )
    
    # 정상 데이터
    return Response(
        os.read(urandom, how_many_bytes),
        content_type="application/octet-stream"
    )
```

### 2단계: hasher에서 입력 검증

hasher POST /에 입력 검증 로직 추가:

```ruby
post '/' do
  body = request.body.read
  
  # 입력 검증
  if body.empty?
    span = OpenTelemetry::Trace.current_span
    span.set_attribute('error.type', 'ValidationError')
    span.set_attribute('error.message', 'Empty input from upstream service')
    span.status = OpenTelemetry::Trace::Status.error('Validation failed')
    
    status 400
    return "ERROR: Empty input not allowed\n"
  end
  
  if body.length < 16  # 최소 16 bytes 필요
    span = OpenTelemetry::Trace.current_span
    span.set_attribute('error.type', 'ValidationError')
    span.set_attribute('error.message', "Input too short: #{body.length} bytes")
    span.status = OpenTelemetry::Trace::Status.error('Validation failed')
    
    status 400
    return "ERROR: Input too short\n"
  end
  
  # 정상 처리
  sleep 2
  content_type 'text/plain'
  "#{Digest::SHA2.new().update(body)}"
end
```

### 3단계: 전체 trace 흐름

```
worker → rng GET /32
  ↓ (RNG_CORRUPTION_RATE=0.1 설정 시 10% 확률로 빈 데이터)
worker ← rng (빈 데이터 또는 정상 데이터)
  ↓
worker → hasher POST / (빈 데이터 전달)
  ↓ (hasher가 입력 검증)
worker ← hasher HTTP 400 (ValidationError)
  ↓
X-Ray trace: worker → rng (OK) → worker → hasher (FAULT)
```

**장점:**
- 실제 서비스 호출 체인 전체(worker → rng → worker → hasher) 활용
- trace context 전파 보장 (worker ADOT가 모든 호출 추적)
- 데이터 오염이 upstream(rng)에서 발생 → downstream(hasher)에서 검증 실패
- 실제 운영 환경에서 발생 가능한 시나리오 (upstream 서비스가 잘못된 데이터 반환)
- DevOps Agent가 발견 가능: "rng가 빈 데이터를 반환 → hasher 검증 실패"
- 정상 동작에 영향 없음 (RNG_CORRUPTION_RATE 미설정 시)

**테스트 시나리오:**
```bash
# 1. rng 데이터 오염 모드 활성화 (10% 확률)
kubectl set env deployment/rng RNG_CORRUPTION_RATE=0.1

# 2. worker가 자동으로 rng → hasher 호출
#    10% 확률로 hasher 검증 실패 → X-Ray에 fault trace 생성

# 3. 대량 호출로 fault trace 확보 (sampling 고려)
#    worker가 초당 ~3-5회 호출하므로 1-2분 대기

# 4. DevOps Agent가 fault trace 발견 확인
#    - worker → rng (OK)
#    - worker → hasher (FAULT: ValidationError)

# 5. 정상 모드 복원
kubectl set env deployment/rng RNG_CORRUPTION_RATE-
```

**trigger-scenarios.sh 추가:**
```bash
60|corrupted-data)
  echo "=== Scenario: Corrupted Data from RNG ==="
  echo "Enabling data corruption mode on rng (10% rate)..."
  kubectl set env deployment/rng RNG_CORRUPTION_RATE=0.1 -n dockercoins
  echo ""
  echo "Worker will now receive corrupted data from rng occasionally."
  echo "Hasher will reject invalid input → fault traces in X-Ray"
  echo ""
  echo "To restore: kubectl set env deployment/rng RNG_CORRUPTION_RATE- -n dockercoins"
  ;;
```
