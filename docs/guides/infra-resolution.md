# 인프라 Resolution 체인

## 개요

서비스 요청이 실제 kubectl/AWS 명령으로 변환되기까지의 4-Layer resolution 체인이다.
"hasher 서비스 로그 보여줘" → 어느 계정, 어느 클러스터, 어느 네임스페이스, 어떤 credential로 접근할지 결정한다.

---

## 1. 전체 Resolution 흐름

```
서비스 요청 ("hasher")
  │
  ▼
TopologyProvider.resolve("hasher")
  → ServiceLocation(account_id, context, namespace)
  │
  ▼
AccountRegistry.get(account_id)
  → RegisteredAccount(profile, role_arn, account_type)
  │
  ▼
CredentialResolver.get_session(account_id)
  → boto3.Session (profile or STS AssumeRole)
  │
  ▼
kubectl --context <arn> -n <namespace> logs hasher-pod
```

---

## 2. Layer 1 — AccountRegistry (`account_registry.py`)

### 소스 우선순위

| 순위 | 소스 | 설명 |
|------|------|------|
| 1 | config.yaml `clusters.*` | 명시적 설정 (최우선) |
| 2 | config/*.env 파일 | profile → account_id 매핑 |
| 3 | Agent Space associations | 동적 발견 (devops-agent API) |

### RegisteredAccount 데이터

```python
RegisteredAccount(
    account_id="123456789012",
    profile="dev",               # AWS CLI 프로파일
    role_arn="arn:...",          # STS assumable role
    account_type="primary",      # primary | secondary
    source="config_yaml",        # 출처
    contexts=["arn:aws:eks:..."],  # kubectl context 목록
    clusters=[{context, label, services}]
)
```

### 주요 API

| 메서드 | 반환 |
|--------|------|
| `get(account_id)` | RegisteredAccount |
| `get_by_context(context)` | RegisteredAccount |
| `get_account_for_context(context)` | account_id |
| `get_primary()` | Primary account |
| `list_all()` | 전체 계정 |

---

## 3. Layer 2 — TopologyProvider (`topology_provider.py`)

### 발견 방법 우선순위

| 순위 | 방법 | 설명 |
|------|------|------|
| 1 | config.yaml `clusters[].services[]` | 정적 라우팅 override |
| 2 | kubectl discovery | Deployments 스캔 (실시간) |
| 3 | Agent Space tagged resources | (Future) |

### ServiceLocation 데이터

```python
ServiceLocation(
    service_name="hasher",
    account_id="123456789012",
    context="arn:aws:eks:us-east-1:123456789012:cluster/main",
    namespace="dockercoins",
    replicas=3
)
```

### 주요 API

| 메서드 | 반환 |
|--------|------|
| `resolve(service_name)` | ServiceLocation |
| `resolve_account(service_name)` | account_id |
| `resolve_context(service_name)` | kubectl context |
| `resolve_profile(service_name)` | AWS profile |

---

## 4. Layer 3 — CredentialResolver (`credential_resolver.py`)

### Session 전략

```
1. profile 존재? → boto3.Session(profile_name=profile)
2. role_arn assumable? → STS AssumeRole
3. 둘 다 없음 → boto3.Session(region=AWS_REGION) (기본)
```

### Role Assumption 제약

- Agent Space role은 서비스 주체 전용 (dashboard에서 assume 불가)
- config.yaml에 명시된 role만 assume 허용
- `list_associations`에서 나온 role은 assume하지 않음

### 캐싱

- **TTL: 55분** (1시간 만료 5분 전 refresh)
- Per-account 캐시
- 만료 시 자동 갱신

### 주요 API

| 메서드 | 반환 |
|--------|------|
| `get_session(account_id)` | boto3.Session (캐시) |
| `get_fis_client(account_id)` | FIS client |
| `get_cw_client(account_id)` | CloudWatch client |

---

## 5. Layer 4 — ClusterManager (`cluster_manager.py`)

### 역할

AccountRegistry + TopologyProvider + CredentialResolver를 조합하여 단일 인터페이스 제공.

### 주요 API

| 메서드 | 역할 |
|--------|------|
| `get_context_for_service(svc)` | 서비스 → kubectl context |
| `get_profile_for_service(svc)` | 서비스 → AWS profile |
| `get_session_for_service(svc)` | 서비스 → boto3 Session |
| `get_namespace_for_service(svc)` | 서비스 → K8s namespace |

---

## 6. 설정 (config.yaml)

### 관련 섹션

```yaml
aws:
  profile: deploy-profile          # Primary 계정 프로파일
  region: us-east-1
  account_id: "123456789012"
  mgmt_profile: mgmt              # Organizations API용
  account_profiles:                # account_id → profile 매핑
    "234567890123": "secondary"

clusters:
  "main@123456789012":
    account_id: "123456789012"
    context: "arn:aws:eks:us-east-1:123456789012:cluster/main"
    profile: "deploy-profile"
    cluster_name: "main"
    services:                      # 정적 라우팅 (override)
      - name: hasher
        namespace: dockercoins
```

---

## 7. 설정 Override 우선순위

| 순위 | 소스 | 예시 |
|------|------|------|
| 1 (최고) | 환경변수 | AGENT_SPACE_ID, AWS_REGION |
| 2 | config.yaml | 사용자 편집 가능 |
| 3 | Agent Space DDB | space-meta-{id} 레코드 |
| 4 (최저) | 하드코딩 기본값 | us-east-1, "dockercoins" |

---

## 8. Setup Wizard (`routes_setup.py`)

### 초기 설정 흐름

1. **Profile Discovery** → `~/.aws/config`, `~/.aws/credentials` 스캔
2. **Credential Validation** → STS GetCallerIdentity
3. **Infrastructure Discovery** → DynamoDB 테이블 검색 (prefix: "frontier-agent-hub-*")
4. **CLI Detection** → Claude Code / Kiro CLI 존재 확인
5. **EKS Auto-Discovery** → 계정별 클러스터 목록 + kubeconfig 생성

### DynamoDB 자동 프로비저닝

- 테이블 미존재 시 자동 생성
- 스키마: PK(`run_id`) + SK(`record_type`) + GSI(`scenario-id-index`)
- Billing: PAY_PER_REQUEST
- ACTIVE 상태 대기 후 진행

---

## 9. 주요 함수 참조

| 함수/클래스 | 파일 | 역할 |
|------------|------|------|
| AccountRegistry (registry) | account_registry.py | 계정 등록/조회 |
| TopologyProvider | topology_provider.py | 서비스→위치 매핑 |
| CredentialResolver | credential_resolver.py | 세션/credential 관리 |
| ClusterManager | cluster_manager.py | 통합 인터페이스 |
| _boto_session | app_config.py | Primary session 헬퍼 |
| is_configured | routes_setup.py | Setup 완료 여부 |

---

## 10. 설계 원칙

1. **Config > Discovery**: 명시적 설정이 동적 발견보다 우선 (의도가 현실을 override)
2. **Profile 우선**: 로컬 SSO profile 선호 (STS round-trip 없음)
3. **Role guard**: Agent Space role은 dashboard에서 assume하지 않음 (서비스 주체 전용)
4. **55분 TTL**: 1시간 만료 직전에 갱신하여 credential 만료 방지
5. **Per-service resolution**: 시나리오 단위가 아닌 서비스 단위로 context 결정
