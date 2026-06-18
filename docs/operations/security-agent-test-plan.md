# AWS Security Agent 테스트 계획

## 개요

AWS Security Agent는 개발 라이프사이클 전반에 걸쳐 애플리케이션 보안을 사전에 확보하는 AI 기반 도구입니다.
현재 Preview 상태이며, us-east-1 리전에서만 사용 가능합니다.

### CloudFormation 지원 현황
| Agent | CloudFormation 지원 | 상태 |
|-------|-------------------|------|
| DevOps Agent | ✅ `AWS::DevOpsAgent::AgentSpace`, `AWS::DevOpsAgent::Association` | Preview (2025.12.02 추가) |
| Security Agent | ❌ 미지원 | Preview (콘솔에서만 설정) |

## Security Agent 핵심 기능

### 1. Design Security Review (설계 보안 검토)
- 아키텍처 문서 분석
- 보안 요구사항 위반 검출
- CloudFormation/Terraform 템플릿 검토

### 2. Code Security Review (코드 보안 검토)
- GitHub 연동을 통한 코드 분석
- SAST (Static Application Security Testing)
- 보안 취약점 및 모범 사례 위반 검출

### 3. Penetration Testing (침투 테스트)
- 배포된 애플리케이션 대상 동적 테스트
- DAST (Dynamic Application Security Testing)
- 컨텍스트 인식 공격 체인 실행

---

## 테스트 환경

### 대상 애플리케이션: DockerCoins
마이크로서비스 기반 암호화폐 채굴 시뮬레이션 앱

| 서비스 | 언어 | 역할 |
|--------|------|------|
| hasher | Ruby (Sinatra) | 해시 생성 |
| rng | Python (Flask) | 난수 생성 |
| worker | Python | 작업 조율 |
| webui | Node.js | 웹 UI |
| redis | Redis | 메시지 큐 |

### 인프라 구성
- VPC + EKS + RDS (PostgreSQL)
- CloudFormation으로 관리
- Kubernetes 매니페스트로 앱 배포

---

## 보안 요구사항 정의

### 일반적인 회사 보안 요구사항


#### SR-001: 인증 및 접근 제어
| ID | 요구사항 | 심각도 |
|----|---------|--------|
| SR-001-1 | 모든 API 엔드포인트는 인증 필요 | Critical |
| SR-001-2 | 비밀번호는 최소 12자, 복잡성 요구 | High |
| SR-001-3 | 세션 타임아웃 30분 이내 | Medium |
| SR-001-4 | 실패한 로그인 시도 5회 후 계정 잠금 | High |

#### SR-002: 데이터 보호
| ID | 요구사항 | 심각도 |
|----|---------|--------|
| SR-002-1 | 전송 중 데이터 암호화 (TLS 1.2+) | Critical |
| SR-002-2 | 저장 데이터 암호화 (AES-256) | Critical |
| SR-002-3 | 민감 데이터 로깅 금지 | High |
| SR-002-4 | PII 데이터 마스킹 | High |

#### SR-003: 비밀 관리
| ID | 요구사항 | 심각도 |
|----|---------|--------|
| SR-003-1 | 하드코딩된 비밀번호/API 키 금지 | Critical |
| SR-003-2 | Secrets Manager 또는 환경변수 사용 | High |
| SR-003-3 | 비밀 로테이션 90일 이내 | Medium |
| SR-003-4 | 소스 코드에 비밀 커밋 금지 | Critical |

#### SR-004: 네트워크 보안
| ID | 요구사항 | 심각도 |
|----|---------|--------|
| SR-004-1 | 최소 권한 Security Group 규칙 | High |
| SR-004-2 | 0.0.0.0/0 인바운드 규칙 금지 (ALB 제외) | High |
| SR-004-3 | 프라이빗 서브넷에 데이터베이스 배치 | Critical |
| SR-004-4 | VPC 엔드포인트 사용 권장 | Medium |

#### SR-005: IAM 및 권한
| ID | 요구사항 | 심각도 |
|----|---------|--------|
| SR-005-1 | 최소 권한 원칙 적용 | High |
| SR-005-2 | 와일드카드(*) 권한 금지 | High |
| SR-005-3 | 서비스별 IAM 역할 분리 | Medium |
| SR-005-4 | IRSA (IAM Roles for Service Accounts) 사용 | High |

#### SR-006: 컨테이너 보안
| ID | 요구사항 | 심각도 |
|----|---------|--------|
| SR-006-1 | 비루트 사용자로 컨테이너 실행 | High |
| SR-006-2 | 읽기 전용 루트 파일시스템 | Medium |
| SR-006-3 | 권한 상승 금지 (allowPrivilegeEscalation: false) | High |
| SR-006-4 | 리소스 제한 설정 (CPU, Memory) | Medium |

#### SR-007: 로깅 및 모니터링
| ID | 요구사항 | 심각도 |
|----|---------|--------|
| SR-007-1 | 모든 API 호출 로깅 | High |
| SR-007-2 | CloudTrail 활성화 | Critical |
| SR-007-3 | 보안 이벤트 알람 설정 | High |
| SR-007-4 | 로그 보존 기간 최소 90일 | Medium |

#### SR-008: 입력 검증
| ID | 요구사항 | 심각도 |
|----|---------|--------|
| SR-008-1 | 모든 사용자 입력 검증 | Critical |
| SR-008-2 | SQL Injection 방지 | Critical |
| SR-008-3 | XSS (Cross-Site Scripting) 방지 | High |
| SR-008-4 | CSRF 토큰 사용 | High |

---

## 현재 코드베이스의 알려진 보안 이슈

Security Agent가 발견해야 할 의도적인 취약점:

### 1. 하드코딩된 비밀번호 (SR-003-1 위반)
```yaml
# infrastructure/cloudformation/03-rds-database.yml:24
MasterUserPassword:
  Type: String
  Default: 'DevOpsAgent2024!'  # ❌ 하드코딩된 비밀번호
```

### 2. 과도한 Security Group 규칙 (SR-004-2 위반)
```yaml
# infrastructure/cloudformation/01-vpc-foundation.yml
CidrIp: 0.0.0.0/0  # ❌ 모든 IP에서 접근 허용
```

### 3. 과도한 IAM 권한 (SR-005-2 위반)
```yaml
# infrastructure/cloudformation/02-eks-platform.yml
ManagedPolicyArns:
  - arn:aws:iam::aws:policy/SecretsManagerReadWrite  # ❌ 과도한 권한
  - arn:aws:iam::aws:policy/CloudWatchFullAccess     # ❌ 과도한 권한
```

### 4. Pod Security Context 미설정 (SR-006 위반)
```yaml
# infrastructure/kubernetes/dockercoins/*.yaml
# ❌ securityContext 미설정
```

### 5. Redis 인증 없음 (SR-001-1 위반)
```yaml
# infrastructure/kubernetes/dockercoins/redis.yaml
# ❌ Redis 비밀번호 미설정
```

### 6. Flask Debug 모드 (SR-002-3 위반)
```python
# services/dockercoins/rng/rng.py
if os.environ.get('FLASK_DEBUG'):
    app.run(debug=True)  # ❌ 프로덕션에서 디버그 모드
```

---

## 테스트 단계


### Phase 1: Security Agent 설정 (콘솔)

Security Agent는 CloudFormation을 지원하지 않으므로 콘솔에서 수동 설정 필요.

#### Step 1.1: Agent Space 생성
1. AWS Console → Security Agent 접속
2. "Create agent space" 클릭
3. 이름: `devops-agent-test-security-space`
4. 설명: `Security testing for DockerCoins application`

#### Step 1.2: 보안 요구사항 등록
1. Agent Space 선택 → "Security Requirements" 탭
2. 위에서 정의한 SR-001 ~ SR-008 요구사항 등록
3. 각 요구사항에 심각도 설정

#### Step 1.3: GitHub 연동 (Code Review용)
1. "Integrations" 탭 → "Add integration"
2. GitHub 선택 → OAuth 인증
3. 레포지토리 선택: `sorididim11/frontier-devops-agent-test-app`

#### Step 1.4: 애플리케이션 등록 (Pentest용)
1. "Applications" 탭 → "Add application"
2. 애플리케이션 URL 입력 (EKS ALB 엔드포인트)
3. 테스트 범위 정의

---

### Phase 2: Design Security Review

CloudFormation 템플릿과 아키텍처 문서를 분석하여 보안 위반 검출.

#### 테스트 대상 파일
| 파일 | 예상 발견 이슈 |
|------|---------------|
| `01-vpc-foundation.yml` | 0.0.0.0/0 Security Group 규칙 |
| `02-eks-platform.yml` | 과도한 IAM 권한 |
| `03-rds-database.yml` | 하드코딩된 비밀번호 |
| `04-devops-agent.yml` | ReadOnlyAccess 정책 (과도할 수 있음) |

#### 검증 체크리스트
- [ ] SR-003-1: 하드코딩된 비밀번호 검출
- [ ] SR-004-2: 0.0.0.0/0 규칙 검출
- [ ] SR-005-2: 와일드카드 권한 검출
- [ ] SR-004-3: 데이터베이스 서브넷 배치 확인

---

### Phase 3: Code Security Review

GitHub 연동을 통한 소스 코드 보안 분석.

---

## 실제 앱 소스코드 보안 취약점 분석

Security Agent Code Review에서 발견해야 할 **실제 취약점**들입니다.

### 1. rng/rng.py - Python Flask 서비스

#### 취약점 1.1: Debug 모드 활성화 가능 (CWE-489)
```python
# Line 11
app.debug = os.environ.get("DEBUG", "").lower().startswith('y')
```
- **위험**: 프로덕션에서 DEBUG=yes 설정 시 스택 트레이스 노출
- **심각도**: Medium
- **권장**: 프로덕션 환경에서 debug 모드 강제 비활성화

#### 취약점 1.2: 하드코딩된 DB 자격증명 기본값 (CWE-798)
```python
# Lines 18-22
DB_HOST = os.environ.get("DB_HOST", "")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "devopsagentdb")
DB_USER = os.environ.get("DB_USER", "dbadmin")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
```
- **위험**: DB 이름, 사용자명이 하드코딩되어 정보 노출
- **심각도**: Low (비밀번호는 빈 문자열이지만 사용자명 노출)
- **권장**: Secrets Manager에서 모든 DB 설정 로드

#### 취약점 1.3: 입력 검증 없는 정수 변환 (CWE-20)
```python
# Line 83
count = int(request.args.get('count', 5))

# Line 127
max_connections = int(request.args.get('max', 100))
```
- **위험**: 악의적 입력 시 예외 발생 또는 DoS
- **심각도**: Medium
- **권장**: 입력 범위 검증 및 예외 처리

#### 취약점 1.4: 무제한 리소스 할당 (CWE-770)
```python
# Line 168-173 (/oom 엔드포인트)
while True:
    memory_hog.append('x' * chunk_size)
```
- **위험**: 의도적 DoS 엔드포인트 (테스트용이지만 프로덕션 노출 위험)
- **심각도**: High
- **권장**: 테스트 엔드포인트 프로덕션 비활성화 또는 인증 필요

#### 취약점 1.5: 예외 메시지에 민감 정보 노출 (CWE-209)
```python
# Line 100
return Response(f"ERROR: Database query failed - {str(e)}\n", status=500)
```
- **위험**: DB 연결 오류 상세 정보가 클라이언트에 노출
- **심각도**: Medium
- **권장**: 일반적인 오류 메시지 반환, 상세 로그는 서버에만

### 2. hasher/hasher.rb - Ruby Sinatra 서비스

#### 취약점 2.1: Rack Protection 비활성화 (CWE-352)
```ruby
# Line 44
set :protection, false
```
- **위험**: CSRF, XSS 등 기본 보호 비활성화
- **심각도**: High
- **권장**: 필요한 보호만 선택적 비활성화

#### 취약점 2.2: 모든 호스트 허용 (CWE-942)
```ruby
# Line 47
set :host_authorization, { permitted_hosts: [] }
```
- **위험**: Host Header Injection 공격 가능
- **심각도**: Medium
- **권장**: 허용된 호스트 명시적 지정

#### 취약점 2.3: 입력 검증 없이 요청 본문 읽기 (CWE-20)
```ruby
# Line 52
"#{Digest::SHA2.new().update(request.body.read)}"
```
- **위험**: 대용량 요청 본문으로 DoS 가능
- **심각도**: Medium
- **권장**: Content-Length 제한 설정

#### 취약점 2.4: 프로세스 강제 종료 엔드포인트 (CWE-749)
```ruby
# Line 66
Process.exit!(1)
```
- **위험**: 인증 없이 서비스 종료 가능
- **심각도**: Critical (테스트용이지만)
- **권장**: 테스트 엔드포인트 인증 필요 또는 프로덕션 제거

### 3. worker/worker.py - Python 워커 서비스

#### 취약점 3.1: 광범위한 예외 처리 (CWE-396)
```python
# Line 52-56
try:
    work_loop()
except:
    log.exception("In work loop:")
```
- **위험**: 모든 예외를 catch하여 예상치 못한 동작 가능
- **심각도**: Low
- **권장**: 특정 예외만 처리

#### 취약점 3.2: 하드코딩된 서비스 URL (CWE-798)
```python
# Lines 20, 26
r = requests.get("http://rng/32")
r = requests.post("http://hasher/", ...)
```
- **위험**: 서비스 URL이 코드에 하드코딩
- **심각도**: Low
- **권장**: 환경 변수로 설정

#### 취약점 3.3: HTTP 응답 검증 없음 (CWE-754)
```python
# Lines 20-21, 26-28
r = requests.get("http://rng/32")
return r.content  # 상태 코드 확인 없음
```
- **위험**: 오류 응답을 정상으로 처리할 수 있음
- **심각도**: Medium
- **권장**: `r.raise_for_status()` 추가

### 4. webui/webui.js - Node.js Express 서비스

#### 취약점 4.1: Redis 연결 오류 처리 미흡 (CWE-755)
```javascript
// Lines 6-8
client.on("error", function (err) {
    console.error("Redis error", err);
});
```
- **위험**: 오류 로깅만 하고 복구 로직 없음
- **심각도**: Low
- **권장**: 재연결 로직 추가

#### 취약점 4.2: 콜백 오류 무시 (CWE-252)
```javascript
// Lines 14-19
client.hlen('wallet', function (err, coins) {
    client.get('hashes', function (err, hashes) {
        // err 변수 사용 안 함
```
- **위험**: Redis 오류 시 undefined 값 반환
- **심각도**: Medium
- **권장**: 오류 처리 추가

#### 취약점 4.3: 정적 파일 서빙 보안 헤더 없음 (CWE-693)
```javascript
// Line 23
app.use(express.static('files'));
```
- **위험**: X-Content-Type-Options, CSP 등 보안 헤더 없음
- **심각도**: Medium
- **권장**: helmet 미들웨어 추가

#### 취약점 4.4: Rate Limiting 없음 (CWE-770)
- **위험**: API 엔드포인트에 요청 제한 없음
- **심각도**: Medium
- **권장**: express-rate-limit 추가

---

### 취약점 요약 테이블

| 파일 | CWE | 취약점 | 심각도 |
|------|-----|--------|--------|
| rng.py | CWE-489 | Debug 모드 활성화 가능 | Medium |
| rng.py | CWE-798 | 하드코딩된 DB 설정 | Low |
| rng.py | CWE-20 | 입력 검증 없음 | Medium |
| rng.py | CWE-770 | 무제한 리소스 할당 | High |
| rng.py | CWE-209 | 오류 메시지 정보 노출 | Medium |
| hasher.rb | CWE-352 | Rack Protection 비활성화 | High |
| hasher.rb | CWE-942 | 모든 호스트 허용 | Medium |
| hasher.rb | CWE-20 | 입력 크기 제한 없음 | Medium |
| hasher.rb | CWE-749 | 위험한 기능 노출 | Critical |
| worker.py | CWE-396 | 광범위한 예외 처리 | Low |
| worker.py | CWE-754 | HTTP 응답 검증 없음 | Medium |
| webui.js | CWE-252 | 콜백 오류 무시 | Medium |
| webui.js | CWE-693 | 보안 헤더 없음 | Medium |
| webui.js | CWE-770 | Rate Limiting 없음 | Medium |

---

#### 검증 체크리스트
- [ ] CWE-489: Debug 모드 활성화 검출
- [ ] CWE-798: 하드코딩된 자격증명 검출
- [ ] CWE-20: 입력 검증 부재 검출
- [ ] CWE-352: CSRF 보호 비활성화 검출
- [ ] CWE-209: 오류 메시지 정보 노출 검출
- [ ] CWE-770: 리소스 제한 없음 검출

---

### Phase 4: Penetration Testing

배포된 애플리케이션에 대한 동적 보안 테스트.

#### 사전 조건
1. DockerCoins 앱이 EKS에 배포되어 있어야 함
2. ALB를 통해 외부 접근 가능해야 함
3. 테스트 범위가 명확히 정의되어야 함

#### 테스트 범위
| 엔드포인트 | 서비스 | 테스트 항목 |
|-----------|--------|------------|
| `/` | webui | XSS, CSRF |
| `/32` | rng | 입력 검증, 에러 처리 |
| `/hash` | hasher | 입력 검증 |

#### 검증 체크리스트
- [ ] 인증 우회 시도
- [ ] SQL Injection 시도
- [ ] XSS 공격 시도
- [ ] 디렉토리 트래버설 시도
- [ ] 서비스 거부 (DoS) 취약점

---

## 테스트 결과 기록 템플릿

### Design Review 결과
| 요구사항 ID | 파일 | 발견 여부 | 심각도 | 권장 조치 |
|------------|------|----------|--------|----------|
| SR-003-1 | 03-rds-database.yml | | Critical | Secrets Manager 사용 |
| SR-004-2 | 01-vpc-foundation.yml | | High | CIDR 범위 제한 |
| SR-005-2 | 02-eks-platform.yml | | High | 최소 권한 정책 |

### Code Review 결과
| 요구사항 ID | 파일 | 라인 | 발견 여부 | 심각도 | 권장 조치 |
|------------|------|------|----------|--------|----------|
| SR-002-3 | rng.py | | | High | Debug 모드 제거 |
| SR-008-1 | hasher.rb | | | Critical | 입력 검증 추가 |

### Pentest 결과
| 취약점 유형 | 엔드포인트 | 발견 여부 | 심각도 | 재현 단계 |
|------------|-----------|----------|--------|----------|
| XSS | /webui | | | |
| SQL Injection | /rng | | | |

---

## 다음 단계

1. **Phase 1 완료 후**: Design Review 실행 및 결과 기록
2. **Phase 2 완료 후**: Code Review 실행 및 결과 기록
3. **Phase 3 완료 후**: Pentest 실행 및 결과 기록
4. **전체 완료 후**: 발견된 취약점 수정 및 재테스트

---

## 참고 자료

- [AWS Security Agent 공식 블로그](https://aws.amazon.com/blogs/aws/new-aws-security-agent-secures-applications-proactively-from-design-to-deployment-preview/)
- [AWS Security Agent 발표](https://aws.amazon.com/about-aws/whats-new/2025/12/aws-security-agent-preview/)
- [Service Authorization Reference - AWS Security Agent](https://docs.aws.amazon.com/service-authorization/latest/reference/list_awssecurityagent.html)
