# Security 시스템

## 개요

Security 시스템은 DevOps Space에 연결된 Security Agent Space를 통해 **보안 스캔(pentest) → 취약점 발견 → 분석(체인 공격, 수정 우선순위) → 코드 수정**까지의 파이프라인을 제공한다.

---

## 1. Space 연결 모델

### DevOps ↔ Security (1:N)

```
DevOps Space A
  ├── Security Space 1 (web app pentest)
  ├── Security Space 2 (API pentest)
  └── Security Space 3 (infra audit)
```

- DevOps Space는 독립 존재
- Security Space는 반드시 하나의 DevOps Space에 연결
- DDB `space-meta-{devops_space_id}`의 `security_links[]`에 저장

### security_links[] 스키마

```json
[
  {
    "security_space_id": "as-xxx",
    "name": "dev-security",
    "target_domain": "app.internal.com",
    "pentest_id": "pt-xxx",
    "repo": {"owner": "org", "name": "repo", "repo_id": "rid"},
    "linked_at": "2024-01-15T10:00:00Z"
  }
]
```

하위 호환: `security_agent_space_id` (scalar) fallback 지원

---

## 2. Target 관리

### Target 종류

| 타입 | 설명 | 검증 방법 |
|------|------|-----------|
| Target Domain | DNS 도메인 (pentest 범위) | Route53 DNS-TXT 레코드 |
| Target Endpoint | HTTP/HTTPS URL | 접근성 확인 |
| Target Repository | GitHub 리포 | Integration API |
| AWS Resource | IAM, VPC, SG 등 | 리소스 존재 확인 |

### 등록 흐름

```
DevOps Space → GitHub 리포 추출 (associations)
  → Security Agent Space 생성
  → GitHub Integration 등록 (integrated_resources)
  → Target Domain 등록 (DNS-TXT 검증)
  → Pentest 생성 (endpoints + repos as assets)
```

---

## 3. Pentest 실행

### 실행 흐름

```
POST /api/settings/security/pentest/start
  → create_pentest_job(space_id, pentest_id)
  → IN_PROGRESS (중복 방지: 기존 IN_PROGRESS면 거부)
  → Job 완료 시 findings 수집 가능
```

### Task Timeline

| Phase | 설명 |
|-------|------|
| PREFLIGHT | 환경 검증 |
| STATIC_ANALYSIS | 코드 정적 분석 |
| PENTEST | 동적 침투 테스트 |
| FINALIZING | 결과 정리/보고 |

---

## 4. Findings (취약점 발견)

### 수집 흐름

```
Security Agent API
  → list_pentests + list_pentest_jobs
  → Latest completed job (pentestJobId)
  → list_findings + batch_get_findings
  → Enrichment (risk 조정, PR 링크, 운영 컨텍스트)
  → DDB 캐시 저장
```

### 캐시 전략

| 계층 | TTL | 용도 |
|------|-----|------|
| Memory | 5분 (job_id 동일 시) | 빠른 반복 조회 |
| Disk | `.skill-cache/{space_id}.json` | 재시작 생존 |
| DDB | 영구 | 첫 로드 시 저장 |

### Enrichment

- Risk 재평가 (remediation 상태 반영)
- PR 링크 추출 (codeRemediationTask)
- 운영 컨텍스트 (endpoint 기반)
- attackScript URL 파싱

---

## 5. Insights 분석

### Chain Attack 분석 (`_compute_chains`)

- "chaining" / "attack chain" 키워드가 포함된 findings 탐색
- attackScript에서 Step 1, Step 2 구조 추출
- 개별 findings를 chain의 component로 매칭
- **Escalation scoring**: 개별 risk < 결합 chain risk

### Service Topology Overlay

```
Finding endpoint hostname → Service node 매핑
attackScript URL references → Lateral movement edges
Direct targets → Entry points
Name patterns → 분류 (app/db/gateway)
```

### Fix Priority Index (`_compute_fix_priority`)

```
각 finding → [참여하는 chain 목록] (역참조)
Score = Σ(component chain risks) + finding own risk
→ 어떤 수정이 가장 많은 chain을 끊는지 순위화
```

---

## 6. 코드 수정 (Remediation)

### 흐름

```
Finding 선택 → POST /api/settings/security/findings/<id>/remediate
  → SecurityAgent.start_code_remediation()
  → PR 생성 (자동)
  → GET /api/settings/security/findings/<id>/status
  → PR merge 추적
```

### 상태

| Status | 의미 |
|--------|------|
| IN_PROGRESS | PR 생성 중 |
| COMPLETED | PR 생성 완료 |
| MERGED | PR merge 완료 |

---

## 7. GitHub 연동

### DevOps Space → GitHub

- Associations에 `configuration.github` blob
- Fields: owner, repoName, repoId

### Security Space → GitHub

- `list_integrations()` → provider="GITHUB"
- `update_integrated_resources()` → 리포 등록/해제
- Capabilities: leaveComments=true, remediateCode=true

### 매칭 로직

owner + repoName 동일성 체크로 DevOps↔Security 간 리포 연결

---

## 8. 주요 API

| 엔드포인트 | 메서드 | 역할 |
|-----------|--------|------|
| `/api/settings/security/spaces` | GET | Security Space 목록 |
| `/api/settings/security/match/<devops_id>` | GET | DevOps↔Security 매칭 |
| `/api/settings/security/target-domain` | POST | Target domain 등록 |
| `/api/settings/security/pentest/start` | POST | Pentest 시작 |
| `/api/security/insights/findings` | GET | Enriched findings |
| `/api/security/insights/chains` | GET | Chain attack 분석 |
| `/api/security/insights/topology` | GET | 토폴로지 오버레이 |
| `/api/security/insights/fix-priority` | GET | 수정 우선순위 |

---

## 9. 주요 함수 참조

| 함수 | 파일 | 역할 |
|------|------|------|
| _find_security_spaces_for_devops | routes_security_targets.py | DevOps→Security 매칭 |
| _remove_security_link_in_ddb | routes_security_targets.py | 연결 해제 |
| api_enriched_findings | routes_security_insights.py | 취약점 목록 + enrichment |
| _compute_chains | routes_security_insights.py | Chain attack 그래프 |
| _compute_fix_priority | routes_security_insights.py | 수정 우선순위 계산 |
| enrich_findings | routes_security_insights.py | Risk 조정 + 컨텍스트 |

---

## 10. 설계 원칙

1. **1:N 연결**: DevOps Space 하나에 여러 Security Space → 멀티 타겟 전략
2. **캐시 3계층**: Memory → Disk → DDB로 점진적 내구성
3. **Chain 분석**: 개별 취약점이 아닌 공격 체인 관점의 우선순위
4. **Fix by impact**: 가장 많은 chain을 끊는 수정부터 추천
5. **자동 수정**: Finding → PR 생성 → merge 추적까지 자동화
