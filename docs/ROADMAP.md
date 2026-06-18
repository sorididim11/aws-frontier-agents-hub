# Roadmap — Frontier Agent Hub

## 비전

**Git repo 연결 → 가상 팀(개발/보안/운영) 자동 편성 → E2E SDLC 자동화**

모든 이슈(개발 요건, 운영 장애, 보안 취약점)가 같은 파이프라인을 탄다:

```
[발견] → [등록] → [할당] → [실행] → [검증] → [종결]
   ↑                                         │
   └──────── 실패 시 자동 재등록 ──────────────┘
```

---

## Gap 분석 (2026-06-18)

| 비전 요소 | 현재 상태 | Gap |
|-----------|-----------|-----|
| 운영 이슈 발견 + Closed-Loop | ✅ 토폴로지→시나리오→검증→교정 | — |
| 보안 이슈 발견 + 수정 루프 | ✅ Pentest→Chain→PR→재검증 | — |
| 스킬 기반 Agent 능력 확장 | ✅ CRUD + 카탈로그 225개 + 추천 | — |
| 멀티 계정 통합 관제 | ✅ 3계정 Single Pane | — |
| Expert Panel (로컬 개발 지원) | ✅ Claude Code 사이드카 | — |
| 이슈 자동 등록 | ❌ 시각화만 | Jira/내부 등록 연동 필요 |
| 이슈 자동 할당 | ❌ 수동 | 유형→Agent 라우팅 엔진 필요 |
| 개발팀 자동 처리 (Kiro) | ❌ Expert Panel(수동)만 | Kiro API 연동 필요 |
| E2E 이슈 라이프사이클 추적 | ⚠️ Security PR만 | 통합 상태 모델 필요 |
| Git repo = 팀 구성 완료 | ⚠️ 수동 Space 설정 | repo→자동 프로비저닝 필요 |

---

## Phase 1: 이슈 파이프라인 (등록 + 추적 + 시각화)

**목표**: Agent가 발견한 문제를 작업 항목으로 자동 변환하고, 상태를 추적한다.

### 1-1. 통합 이슈 모델

```json
{
  "id": "ISS-xxx",
  "source": "devops_agent | security_agent | manual",
  "type": "operational | security | feature",
  "severity": "critical | high | medium | low",
  "title": "...",
  "description": "...",
  "assignee": "devops_agent | security_agent | kiro | human",
  "status": "open | assigned | in_progress | verifying | resolved | reopened",
  "repo": "org/repo-name",
  "pr_url": "https://...",
  "space_id": "...",
  "created_at": "...",
  "resolved_at": "..."
}
```

### 1-2. 자동 등록 트리거

| 소스 | 트리거 조건 | 이슈 생성 내용 |
|------|------------|----------------|
| DevOps Agent | 시나리오 검증 실패 | 장애 재현됨, 대응 필요 |
| DevOps Agent | 토폴로지 이상 감지 (orphan, missing) | 인프라 정합성 문제 |
| Security Agent | Finding (HIGH/CRITICAL) | 취약점 수정 필요 |
| Security Agent | Chain Attack 식별 | 연쇄 공격 경로 차단 필요 |
| 사람 | 수동 등록 (UI / Jira) | 개발 요건, 기타 |

### 1-3. 외부 연동

- [ ] Jira 양방향 동기화: 이슈 생성 → Jira ticket / Jira 상태 변경 → 이슈 업데이트
- [ ] Webhook 수신: 외부에서 이슈 등록 가능 (GitHub Issues, PagerDuty 등)

### 1-4. 이슈 대시보드

- 전체 이슈 목록 (상태별 필터)
- 타임라인 뷰: 발견→등록→할당→PR→검증→종결 전 이력
- 통계: 유형별/심각도별/Agent별 분포

---

## Phase 2: 자동 할당 + 로컬 개발 연동

**목표**: 이슈가 등록되면 적합한 Agent/도구에게 자동으로 라우팅된다.

### 2-1. 할당 엔진

| 이슈 유형 | 처리 주체 | 방식 |
|-----------|-----------|------|
| 보안 취약점 (코드) | Security Agent | 자동 remediation → PR |
| 운영 장애 (인프라) | DevOps Agent + App | 시나리오 재생성 → 실행 → 검증 |
| 개발 요건 / 버그 | Claude Code (Expert Panel) | 개발자에게 이슈 표시 → 로컬 작업 |

### 2-2. Expert Panel 강화

- [ ] 이슈 목록에서 클릭 → Expert Panel에 컨텍스트 자동 주입
- [ ] 작업 결과(PR) → 이슈 상태 자동 업데이트
- [ ] 검증 루프: PR merge → Security Agent 재스캔 / 시나리오 재실행

### 2-3. 검증 자동 트리거

- PR merge → 관련 이슈의 검증 단계 자동 시작
- 검증 통과 → 이슈 RESOLVED
- 검증 실패 → 이슈 REOPENED + 재할당

---

## Phase 3: Kiro 자율 에이전트 연동

**목표**: 사람 개입 없이 이슈 → 코드 수정 → PR → 검증 → 종결 무인 처리.

- [ ] Kiro Autonomous Agent API 조사 + PoC
- [ ] 이슈 → Kiro 자동 할당 플로우
- [ ] Kiro PR → 검증 루프 연결
- [ ] 무인 처리 모니터링 대시보드
- [ ] 에스컬레이션: Kiro 실패 시 → 사람에게 알림

---

## Phase 4: Git Repo 연결 = 자동화 시작

**목표**: URL 하나 입력하면 가상 팀이 자동 구성된다.

- [ ] Repo 연결 위저드: Git URL → Space 자동 생성 → Agent 3종 프로비저닝
- [ ] Repo 유형 감지: app code / IaC / config → 적합한 Agent 자동 할당
- [ ] 다중 Repo 오케스트레이션: monorepo / multi-repo 지원
- [ ] 팀 구성 시각화: 어떤 repo에 어떤 Agent가 붙어있는지 한눈에

---

## 성공 지표

| 지표 | 목표 |
|------|------|
| 이슈 등록 자동화율 | Agent 발견 → 100% 자동 등록 |
| 평균 해결 시간 (MTTR) | Phase 1: 시각화, Phase 3: 무인 해결 |
| Closed-Loop 완결률 | 발견→종결까지 사람 개입 없이 완결되는 비율 |
| 팀 구성 시간 | Phase 4: repo URL 입력 → 5분 내 가동 |
