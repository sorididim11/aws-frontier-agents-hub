# DevOps Agent Builder — Multi-Agent System

AWS DevOps Agent 구축 전문가 시스템.
Supervisor + Builder + Verifier 3-Agent 아키텍처로 검증된 답변/코드를 생성합니다.

---

## 아키텍처

```
┌─ Supervisor (고객 대면) ──────────────────────────────────┐
│  • 요청 라우팅 + 오케스트레이션                             │
│  • Builder 결과를 Verifier에게 검증 위임                   │
│  • FAIL → Builder 수정 지시 → 재검증 (개선 루프)           │
│  • PASS → 고객에게 전달                                   │
└──────────┬────────────────────────────┬───────────────────┘
           │                            │
           ▼                            ▼
┌─ Builder ─────────────┐   ┌─ Verifier ──────────────────┐
│  답변/코드 생성         │   │  공식 문서 기준 팩트체크     │
│  Skills 기반 워크플로우 │   │  @awsknowledge 실시간 조회  │
│  aws cli 실행 가능      │   │  PASS/FAIL + 근거 반환      │
└─────────────────────────┘   └──────────────────────────────┘
```

---

## 설치

### 사전 요구사항

- Kiro CLI 설치 (`kiro-cli --version` 확인)
- AWS 프로파일 설정 (`aws sts get-caller-identity --profile {profile}` 동작)

### 설치 명령

```bash
# 현재 프로젝트에 설치
chmod +x install.sh
./install.sh .

# 또는 특정 경로에 설치
./install.sh /path/to/customer-project
```

### 수동 설치

```bash
# 1. Agent 등록
cp agents/*.json ~/.kiro/agents/

# 2. 프로젝트에 지식 배포
cp -r .kiro/ /path/to/project/
cp -r skills/ /path/to/project/
```

---

## 사용법

```bash
# 프로젝트 디렉토리에서
kiro-cli chat --agent devops-agent-supervisor
```

### 테스트 프롬프트

| 카테고리 | 프롬프트 |
|----------|----------|
| 이론 | "DevOps Agent란 뭐야?" |
| 이론 (검증 함정) | "Private Connection에 NLB 필요해?" |
| 구축 | "Private GitLab 연결해줘" |
| 구축 | "Splunk Cloud 붙여줘" |
| 코드 실행 | "Splunk CFn 만들고 validate 해줘" |
| 트러블슈팅 | "Association FAILED 상태야. 도와줘" |
| Cross-account | "두 번째 계정을 연결해줘" |
| On-prem | "온프레미스 도구를 통합하고 싶어" |

---

## 패키지 구조

```
poc-delivery/
├── install.sh                          ← 원클릭 설치
├── uninstall.sh                        ← 제거
├── README.md                           ← 이 파일
├── agents/
│   ├── devops-agent-supervisor.json    ← 오케스트레이터
│   ├── devops-agent-builder.json       ← 코드/답변 생성
│   └── devops-agent-verifier.json      ← 팩트체크
├── .kiro/steering/
│   └── devops-agent-expert.md          ← 프로젝트 규칙 (항상 ON)
└── skills/
    ├── devops-agent-reference/SKILL.md ← 공식 스키마 + 알려진 문서 오류
    ├── devops-agent-theory/SKILL.md    ← 이론/설계원칙
    ├── connect-gitlab-private/SKILL.md ← GitLab Private 워크플로우
    ├── connect-splunk/SKILL.md         ← Splunk Cloud 워크플로우
    └── verify-agent/SKILL.md           ← 배포 후 검증/트러블슈팅
```

---

## 검증 루프 상세

```
모든 요청 (이론이든 구축이든)
    │
    ▼
Supervisor → Builder: "답변/코드 생성해"
    │
    ├── 생성됨 → Supervisor → Verifier: "검증해"
    │               │
    │               ├── PASS → 고객에게 전달
    │               └── FAIL + 이유 → Builder: "수정해" → 재검증
    │
    └── "정보 부족" → Supervisor가 고객에게 질문
```

---

## Skills 역할

| Skill | Builder | Verifier | 용도 |
|-------|---------|----------|------|
| reference | ✓ | ✓ | 공식 CFn 스키마 (생성 기준 + 검증 기준) |
| theory | ✓ | - | 이론 설명용 (개념, 설계 원칙) |
| connect-gitlab | ✓ | - | GitLab 구축 워크플로우 |
| connect-splunk | ✓ | - | Splunk 구축 워크플로우 |
| verify-agent | ✓ | - | 배포 후 검증 절차 |

---

## 테스트 결과 (검증됨)

| # | 테스트 | 결과 |
|---|--------|------|
| 1 | On-prem 통합 (VPC Lattice 정확성) | PASS |
| 2 | Splunk CFn + validate-template | PASS |
| 3 | 트러블슈팅 (DNS 에러) | PASS (문서 오류 테이블로 보완) |
| 4 | 정보 부족 → 질문 | PASS |
| 5 | Cross-account (SourceAws) | PASS |

---

## 제거

```bash
chmod +x uninstall.sh
./uninstall.sh
```

---

## 유지보수

### Reference Skill 갱신 (주기적)

```bash
kiro-cli chat --agent devops-agent-verifier \
  "@awsknowledge로 DevOps Agent CFn 리소스 스키마 변경사항 확인해줘"
```

변경 있으면 `skills/devops-agent-reference/SKILL.md` 업데이트.

### 새 데이터소스 추가

1. `skills/connect-{datasource}/SKILL.md` 생성
2. `agents/devops-agent-builder.json`의 resources에 추가
3. reference skill에 해당 Configuration 스키마 추가
