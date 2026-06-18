# Frontier Agent Hub

EKS 기반 마이크로서비스 환경에서 **AI Agent가 장애를 발견하고, 분석하고, 시나리오를 만들고, 실행하고, 검증하는** 전체 과정을 자동화하는 플랫폼.

AWS DevOps Agent(운영)와 Security Agent(보안)를 통합하여, 사람은 방향만 설정하고 Agent가 Closed-Loop으로 인프라를 개선한다.

---

## 핵심 가치

```
[Discover]  EKS 토폴로지 자동 발견 → 서비스, 네트워크, 의존성 그래프 구축
     ↓
[Analyze]   AI가 장애 모드 + 보안 취약점 식별
     ↓
[Generate]  아키텍처 기반 장애 시나리오 자동 생성
     ↓
[Execute]   FIS/kubectl 실행 (Agent가 계획, App이 통제)
     ↓
[Verify]    3-Agent 검증 — 실패 시 자동 교정 후 재실행
     ↺
```

이 순환(Closed-Loop)이 이 앱의 존재 이유다.
AWS DevOps Agent 단독으로는 조사만 하고 끝나지만, 이 앱은 **조사 → 행동 → 검증 → 개선**까지 이어준다.

---

## 설계 원칙

| 원칙 | 왜 |
|------|-----|
| **Closed-Loop 자동화** | 발견-분석-생성-실행-검증이 끊김 없이 순환해야 실질적 개선이 일어남 |
| **Agent = 계획, App = 실행** | Agent에게 인프라 실행 권한을 주면 보안 경계 붕괴. App이 통제해야 rollback/audit 가능 |
| **DevOps + Security 통합** | 장애(운영)와 취약점(보안)은 같은 인프라에서 발생. 분리하면 맥락을 놓침 |
| **스킬 기반 확장** | Agent의 능력을 도메인별 스킬 플러그인으로 확장. 새 분석 도메인 = 새 스킬 추가 |
| **사람-AI 협업** | AI가 자율 실행하되, Expert Panel로 사람이 방향 수정/개입 가능 |
| **Multi-Account 통합** | 여러 AWS 계정의 클러스터를 하나의 UI에서 관리 |

---

## 기능 (12 Blueprint)

| 영역 | Blueprint | 하는 일 |
|------|-----------|---------|
| 발견 | **arch** | EKS 토폴로지 자동 발견, 의존성/네트워크 매핑, 버전 관리 |
| 발견 | **dag** | Investigation DAG — 알람 → 가설 → 증거 수집 시각화 |
| 시나리오 | **scenario** | AI 시나리오 생성 → FIS 실행 → 검증 루프 |
| 시나리오 | **simulation** | 시뮬레이션 엔진 (드라이런 + 안전 정책) |
| 시나리오 | **skills** | Agent 스킬 카탈로그 (CRUD, 추천, 배포) |
| 보안 | **security_targets** | Security Agent Space 연동, 타겟 매칭 |
| 보안 | **security_insights** | SAST 취약점 + Chain Attack + Fix Priority |
| 보안 | **settings** | Pentest 라이프사이클 관리 |
| 관리 | **space** | Agent Space 대시보드, 데이터소스 CRUD |
| 관리 | **cfn_import** | CloudFormation Import/Disconnect |
| 관리 | **expert** | Expert Agent 패널 (Claude Code + Agent Space 채팅) |
| 관리 | **setup** | 초기 설정 위저드 |

---

## 아키텍처

```
config.yaml → app_config.py
  │
  ├→ cluster_manager
  │   ├── account_registry      멀티 계정 통합 (yaml > env > Agent Space)
  │   ├── topology_provider     EKS 서비스 발견 (kubectl, 60s refresh)
  │   └── credential_resolver   계정별 인증 (profile > STS, 55min cache)
  │
  ├→ ai_provider
  │   ├── bedrock_direct        Bedrock Converse + tool loop (로컬 실행)
  │   └── agent_space           DevOps Agent API (daemon thread)
  │
  ├→ multi_agent_engine         Generator + Verifier + Evaluator
  │
  ├→ simulation_engine          resolver + step_runner + orchestrator
  │
  └→ security_enrichment        Security Agent → findings → chain attack
```

---

## 실행

```bash
cd services/dashboard
python overview_app.py   # http://localhost:5003
```

### 환경 변수

| 변수 | 설명 |
|------|------|
| `AWS_PROFILE` | AWS credential 프로필 |
| `CONFIG_PATH` | config.yaml 경로 (기본: `services/dashboard/config.yaml`) |

---

## 프로젝트 구조

```
.
├── services/dashboard/           # 메인 앱 (Flask, port 5003)
│   ├── overview_app.py           # 진입점
│   ├── routes_*.py               # 12 Blueprint API
│   ├── arch_analysis.py          # 토폴로지 발견 엔진
│   ├── multi_agent_engine.py     # 3-Agent 검증
│   ├── simulation_engine/        # 시뮬레이션 (resolver, runner, orchestrator)
│   ├── scenarios/                # 장애 시나리오 JSON
│   └── static/                   # 프론트엔드
├── skills/                       # Agent 스킬
│   ├── arch-discover/            # 아키텍처 발견
│   ├── k8s-detail/               # K8s 상세 분석
│   ├── scenario-generate/        # 시나리오 생성
│   └── rca-*/                    # RCA 분석
├── docs/
│   ├── design/                   # 설계 문서 (ADR, 모듈맵, 엔진 내부)
│   ├── guides/                   # 연동 가이드 (GitLab, Splunk, 계정)
│   └── operations/               # 테스트 시나리오/결과
└── tests/                        # 유닛/통합 테스트
```

---

## 문서

| 문서 | 내용 |
|------|------|
| [설계 결정 (ADR)](docs/design/design-decisions.md) | 왜 이렇게 만들었는지 (DD-001~) |
| [모듈 맵](docs/design/module-map.md) | 전체 Blueprint + 의존성 + 데이터 흐름 |
| [내부 구조 상세](docs/design/frontier-agent-hub-internals.md) | 데이터 모델, Feature Flow, Cross-Cutting 패턴 |
| [시나리오 엔진](docs/design/scenario-engine.md) | 실행 3경로, FM 템플릿, 자동 교정 |
| [스킬 시스템](docs/design/skills.md) | API-first CRUD, 카탈로그, 추천 |
| [보안 모듈](docs/design/security.md) | Pentest → Chain Attack → Remediation |
| [Expert Agent](docs/design/expert-agent.md) | Claude Code 사이드카 |
| [계정 사용법](docs/guides/account-usage.md) | Multi-Account 프로필 설정 |

---

## 배포

### 로컬 개발

```bash
pip install -r services/dashboard/requirements.txt
cd services/dashboard && python overview_app.py
```

### EKS 배포

인프라(VPC, EKS, RDS)와 K8s manifest는 별도 리포:

| 리포 | 용도 |
|------|------|
| [frontier-agent-test-dockercoins](https://github.com/sorididim11/frontier-agent-test-dockercoins) | CloudFormation + Kustomize + 배포 스크립트 |

---

## 보안 정책

- 소스 코드에 AWS Account ID, credential, endpoint를 하드코딩하지 않는다
- 환경별 값은 환경 변수 또는 Secrets Manager로 주입
- 테스트 결과물(Agent 응답)은 `.gitignore`로 리포 제외
- 패스워드는 K8s Secret으로 관리, yaml에 평문 금지
