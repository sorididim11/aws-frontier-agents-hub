# Frontier Agent Hub

**Git repo 하나 연결하면, AI 가상 팀이 개발-보안-운영을 End-to-End로 자동화한다.**

AWS Frontier Agents(DevOps Agent, Security Agent, Kiro)를 3개의 가상 팀으로 편성하고, 이슈 등록부터 수정, 검증, 종결까지 하나의 파이프라인으로 관리하는 SDLC 오케스트레이터.

---

## 왜 필요한가

AWS DevOps Agent는 조사만 하고 끝난다. Security Agent는 취약점을 알려주고 끝난다. Kiro는 코드를 만들고 끝난다.

**각각은 뛰어나지만, 연결이 없다.**

이 앱은 그 연결을 만든다:

```
[발견] → [등록] → [할당] → [실행] → [검증] → [종결]
   ↑                                         │
   └──────── 실패 시 자동 재등록 ──────────────┘
```

어떤 유형의 이슈든(개발 요건, 운영 장애, 보안 취약점) 같은 파이프라인을 탄다.

---

## 3개 가상 팀

| 가상 팀 | Agent | 하는 일 | 연결 방식 |
|---------|-------|---------|-----------|
| **개발팀** | Kiro / Claude Code | 요건 구현, 버그 수정, PR 생성 | Git repo (app code) |
| **보안팀** | Security Agent | 취약점 스캔, 체인 공격 분석, 코드 수정 | Git repo (SAST/DAST) |
| **운영팀** | DevOps Agent | 토폴로지 발견, 장애 조사, RCA | Git repo (IaC/K8s) |

**Git repo 연결 = 가상 팀 구성 완료.** 각 Agent가 repo를 보고 자기 역할을 수행한다.

```
            ┌─────────────────────┐
            │   Frontier Hub      │
            │   (관제탑)           │
            └──────────┬──────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
   ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
   │ Dev AI   │   │ DevOps  │   │Security │
   │(개발팀)  │   │(운영팀) │   │(보안팀) │
   └────┬─────┘   └────┬────┘   └────┬────┘
        │              │              │
        ▼              ▼              ▼
     Git Repo       Git Repo       Git Repo
```

---

## Frontier Hub가 하는 일 (오케스트레이터 역할)

| 역할 | 설명 |
|------|------|
| **자동 등록** | Agent가 발견한 문제를 작업 항목으로 변환 (내부 등록 / Jira) |
| **할당** | 이슈 유형에 맞는 Agent/팀에게 자동 라우팅 |
| **실행 통제** | Agent=계획만 생성, App=실제 실행 (보안 경계) |
| **검증 루프** | 3-Agent 검증 (Generator→Verifier→Evaluator), 실패 시 자동 재생성 |
| **시각화** | 토폴로지, 공격 체인, 이슈 상태를 한 화면에서 |
| **거버넌스** | 감사 추적, 버전 관리, Drift 감지, 권한 분석 |

---

## Agent별 확장 — 이 앱이 추가하는 가치

### DevOps Agent 위에 쌓은 것

DevOps Agent 단독: 질문하면 답하는 수준.

이 앱이 추가:
- **다단계 오케스트레이션** — Q1(앱 식별)→Q2(토폴로지)→검증→보강 자동 순환
- **스킬 주입** — Agent에게 도메인별 분석 규칙을 가르침 (225개 AWS 카탈로그 + 커스텀)
- **토폴로지 기반 시나리오 추천** — 발견된 아키텍처에서 장애 모드 자동 매칭
- **실행 + 검증** — Agent가 읽기만 하는 것을 App이 실행하고, Verifier Agent가 검증
- **영속화 + 시각화** — DDB 버전 관리, L1/L2/L3 뷰, SSE 실시간 스트리밍

### Security Agent 위에 쌓은 것

Security Agent 단독: "이 코드에 취약점이 있다"까지만.

이 앱이 추가:
- **운영 컨텍스트 위험 재평가** — 내부망 전용? NetworkPolicy 있음? → 실질 위험도 조정
- **체인 공격 식별** — 개별 취약점을 넘어 연쇄 공격 경로 + 위험 증폭(escalation) 계산
- **Fix Priority** — 어떤 수정이 가장 많은 공격 체인을 끊는지 순위화
- **토폴로지 오버레이** — 서비스 그래프 위에 보안 상태 시각화
- **수정 루프 완결** — PR 생성→merge 추적→재검증→해결 확인

### 스킬 시스템 — Agent 능력 확장

| 단계 | 설명 |
|------|------|
| 작성 | `skills/<name>/src/` 모듈식 마크다운 |
| 빌드 | src/*.md 정렬 조립 → 단일 SKILL.md |
| 배포 | ZIP → Asset API → Agent Space에 등록 |
| 사용 | Agent가 스킬 규칙에 따라 분석 수행 |
| 추천 | 토폴로지 노드 kind → 카탈로그 225개에서 매칭 |
| 자동 배포 | 분석 시작 시 기본 스킬(arch-discover, k8s-detail) 자동 투입 |

---

## 구현 상태

| 기능 | 상태 | 설명 |
|------|------|------|
| 토폴로지 자동 발견 | ✅ | EKS 멀티 계정, boundary 확장, 버전 관리 |
| 장애 시나리오 자동화 | ✅ | 추천→생성→실행→3-Agent 검증→자동 교정 |
| 보안 스캔 + 체인 분석 | ✅ | Pentest→Findings→Chain Attack→Fix Priority |
| 보안 코드 수정 | ✅ | Finding→PR→Merge 추적→재검증 |
| 스킬 관리 | ✅ | CRUD + 225개 카탈로그 + 토폴로지 추천 |
| 멀티 계정 통합 관제 | ✅ | 3계정 Single Pane of Glass |
| Expert Panel | ✅ | Claude Code 사이드카 (로컬 IDE 연동) |
| 거버넌스 | ✅ | Audit trail, Drift 감지, 권한 분석 |
| 이슈 자동 등록 (Jira) | 🔜 | 로드맵 |
| Kiro 자율 에이전트 연동 | 🔜 | 로드맵 (Phase 2) |
| E2E SDLC 추적 대시보드 | 🔜 | 로드맵 |

### 진화 단계

| Phase | 개발팀 역할 | 설명 |
|-------|------------|------|
| **1 (현재)** | Claude Code CLI / Expert Panel | 이슈 → 개발자가 로컬에서 작업 |
| **2 (추후)** | Kiro Autonomous Agent | 이슈 → 자동 할당 → 무인 처리 |

---

## 설계 원칙

| 원칙 | 왜 |
|------|-----|
| **Agent = 계획, App = 실행** | Agent에게 인프라 실행 권한을 주면 보안 경계 붕괴. App이 통제해야 rollback/audit 가능 |
| **Git repo = 유니버설 인터페이스** | repo 연결만으로 Agent 3종이 각자 역할 시작. 추가 설정 최소화 |
| **Closed-Loop** | 발견→실행→검증이 끊김 없이 순환. 실패하면 자동으로 다시 돈다 |
| **3-Agent 검증** | Generator(생성)+Verifier(검증)+Evaluator(평가)로 AI 출력 신뢰성 확보 |
| **Single-App 경계 확장** | 단일 앱 기준 분석 후 경계 노드를 점진 확장. hallucination 방지 |
| **IaC 기반 진실** | 인프라 상태는 CFn/CDK로 확인. 거짓 fallback 금지 |
| **스킬 기반 능력 확장** | 새 분석 도메인 = 새 스킬 추가. Agent 코드 수정 없이 능력 확장 |

---

## 아키텍처

```
config.yaml → app_config.py
  │
  ├→ cluster_manager
  │   ├── account_registry      멀티 계정 (yaml > env > Agent Space)
  │   ├── topology_provider     서비스 발견 (kubectl, 60s refresh)
  │   └── credential_resolver   계정별 인증 (profile > STS, 55min cache)
  │
  ├→ ai_provider
  │   ├── bedrock_direct        Bedrock Converse + tool loop (로컬 실행)
  │   └── agent_space           DevOps Agent API (daemon thread worker)
  │
  ├→ skill_manager              Asset API CRUD + 카탈로그 + 추천
  │
  ├→ multi_agent_engine         Generator + Verifier + Evaluator
  │
  ├→ simulation_engine          resolver + step_runner + orchestrator
  │
  └→ security_enrichment        위험 재평가 + 체인 공격 + Fix Priority
```

### 12 Blueprint

| 영역 | Blueprint | 역할 |
|------|-----------|------|
| 발견 | **arch** | EKS 토폴로지 자동 발견 + 의존성 매핑 |
| 발견 | **dag** | Investigation DAG 시각화 |
| 시나리오 | **scenario** | 장애 시나리오 생성→실행→검증 |
| 시나리오 | **simulation** | 시뮬레이션 (드라이런 + 안전 정책) |
| 시나리오 | **skills** | Agent 스킬 카탈로그 CRUD + 추천 |
| 보안 | **security_targets** | Security Space 생성/연동/타겟 매칭 |
| 보안 | **security_insights** | 체인 공격 + Fix Priority + 토폴로지 오버레이 |
| 보안 | **settings** | Pentest 라이프사이클 관리 |
| 관리 | **space** | Agent Space 대시보드 + 데이터소스 CRUD |
| 관리 | **cfn_import** | CloudFormation Import/Disconnect |
| 관리 | **expert** | Expert Panel (Claude Code + Agent Space 채팅) |
| 관리 | **setup** | 초기 설정 위저드 |

---

## 실행

```bash
cd services/dashboard
python overview_app.py   # http://localhost:5003
```

| 환경 변수 | 설명 |
|-----------|------|
| `AWS_PROFILE` | AWS credential 프로필 |
| `CONFIG_PATH` | config.yaml 경로 (기본: `services/dashboard/config.yaml`) |

---

## 프로젝트 구조

```
.
├── services/dashboard/           # Flask 대시보드 (메인 앱, port 5003)
│   ├── overview_app.py           # 진입점 + Blueprint 등록
│   ├── routes_*.py               # 12 Blueprint API
│   ├── arch_analysis.py          # 토폴로지 발견 엔진
│   ├── multi_agent_engine.py     # 3-Agent 검증
│   ├── simulation_engine/        # 시뮬레이션 (resolver, runner, orchestrator)
│   ├── skill_manager.py          # 스킬 CRUD + 배포
│   ├── catalog_manager.py        # AWS 스킬 카탈로그
│   ├── security_enrichment.py    # 보안 컨텍스트 재평가
│   ├── scenarios/                # 장애 시나리오 JSON
│   └── static/                   # 프론트엔드
├── skills/                       # Agent 스킬 (모듈식 소스)
│   ├── arch-discover/            # 아키텍처 발견 규칙
│   ├── k8s-detail/               # K8s 상세 분석 규칙
│   ├── scenario-generate/        # 시나리오 생성 규칙
│   └── rca-*/                    # RCA 분석 규칙
├── docs/
│   ├── design/                   # 설계 문서 (ADR, 모듈맵, 내부 구조)
│   ├── guides/                   # 연동 가이드
│   └── operations/               # 테스트 시나리오/결과
└── tests/
```

---

## 문서

| 문서 | 내용 |
|------|------|
| [설계 결정 (ADR)](docs/design/design-decisions.md) | 왜 이렇게 만들었는지 (DD-001~) |
| [모듈 맵](docs/design/module-map.md) | Blueprint + 의존성 + 데이터 흐름 |
| [내부 구조 상세](docs/design/frontier-agent-hub-internals.md) | 데이터 모델, Feature Flow |
| [시나리오 엔진](docs/design/scenario-engine.md) | 실행 3경로, FM 템플릿, 자동 교정 |
| [스킬 시스템](docs/design/skills.md) | API-first CRUD, 카탈로그, 추천 |
| [보안 모듈](docs/design/security.md) | Pentest→Chain Attack→Remediation |
| [Expert Agent](docs/design/expert-agent.md) | Claude Code 사이드카 |

---

## 관련 리포

| 리포 | 용도 |
|------|------|
| [frontier-agent-test-dockercoins](https://github.com/sorididim11/frontier-agent-test-dockercoins) | 테스트 인프라 IaC + K8s manifests |

---

## 보안 정책

- 소스 코드에 AWS Account ID, credential, endpoint를 하드코딩하지 않는다
- 환경별 값은 환경 변수 또는 Secrets Manager로 주입
- Agent는 읽기 전용. 실행 권한은 App이 통제
- 패스워드는 K8s Secret으로 관리
