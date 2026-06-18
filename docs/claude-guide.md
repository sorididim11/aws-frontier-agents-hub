# Claude Code 가이드 체계 사용법

## 개요

Cal.com의 3-tier 모델을 기반으로 구성된 프로젝트 가이드 시스템.
Claude Code가 이 프로젝트에서 규칙을 준수하고, 피드백을 학습하고, 일관된 행동을 하도록 만드는 구조.

---

## 파일 구조

```
프로젝트 루트/
├── CLAUDE.md                       ← 본체 (~100줄). 항상 로드됨
├── .claude/
│   ├── rules/                      ← 경로별 스코프 규칙 (8개)
│   │   ├── routes.md               ← routes_*.py 작업 시 활성화
│   │   ├── arch-analysis.md        ← arch_*.py 작업 시 활성화
│   │   ├── scenario.md             ← scenario/verifier/engine 작업 시
│   │   ├── security.md             ← security_*.py 작업 시
│   │   ├── ai-provider.md          ← ai_provider/providers/chat_worker 작업 시
│   │   ├── skill.md                ← skill_manager/catalog/routes_skills 작업 시
│   │   ├── infra.md                ← cluster/account/topology/credential 작업 시
│   │   └── config.md               ← config.yaml/app_config.py 작업 시
│   ├── commands/                   ← 슬래시 커맨드 (3개)
│   │   ├── run.md                  ← /run — 앱 시작
│   │   ├── restart.md              ← /restart — 안전 재시작
│   │   └── check-module.md         ← /check-module <name> — 모듈 탐색
│   └── settings.json               ← 권한 설정
├── docs/
│   ├── module-map.md               ← 참조: Blueprint, 의존성, 데이터 흐름
│   ├── design-decisions.md         ← 설계 결정 기록 (DD-001~)
│   └── TODO.md                     ← 모듈별 작업 추적
└── ~/.claude/.../memory/           ← 세션 간 축적 지식 (자동 관리)
```

---

## 로딩 순서와 동작 원리

### 1단계: 세션 시작 시 (항상 로드)

```
~/.claude/CLAUDE.md          → 글로벌 행동 원칙 (계획, 검증, 자기개선 루프)
프로젝트/CLAUDE.md           → 프로젝트 행동 지시 (Commands, 3-tier, When Stuck)
~/.claude/.../memory/MEMORY.md → memory 인덱스 (축적된 지식 목록)
```

### 2단계: 파일 접근 시 (on-demand 로드)

```
사용자: "routes_arch.py 수정해줘"
Claude가 routes_arch.py를 Read
    ↓
paths: 매칭:
  - .claude/rules/arch-analysis.md  (paths: services/dashboard/arch_*.py)
  - .claude/rules/routes.md         (paths: services/dashboard/routes_*.py)
    ↓
두 규칙 파일이 컨텍스트에 추가됨
Claude는 이제 "분석 중 편집 금지" + "에러는 명시적으로" 등을 인지
```

### 3단계: 커맨드 호출 시

```
사용자: /restart
    ↓
.claude/commands/restart.md 로드
    ↓
정의된 절차 실행: active-runs 확인 → 경고 → 재시작
```

---

## CLAUDE.md 본체 구조 (3-tier 모델)

| 섹션 | 용도 | Claude 행동 |
|------|------|-------------|
| **Quick Commands** | 실행/재시작/테스트 커맨드 | copy-paste 실행 |
| **Architecture** | 간략 의존성 그래프 + @import | 상세는 module-map.md 참조 |
| **Always Do** | 무조건 지킬 것 | 위반 시 즉시 교정 |
| **Never Do** | 절대 하지 말 것 | 시도 자체를 하지 않음 |
| **Ask First** | 확인 후 진행 | 사용자에게 먼저 물어봄 |
| **설계 결정** | DD 문서 포인터 | 새 결정 시 기록 후 진행 |
| **TODO 관리** | claim 규칙 | 작업 전 TODO 확인 |
| **When Stuck** | 탈출 경로 | 순서대로 참조 |

---

## 피드백 → 규칙 자동 승격 사이클

사용자가 피드백을 주면 Claude가 자동으로 4곳에 반영:

```
사용자: "거짓 fallback 만들지마. IaC + app API 활용해"
         │
         ├─① .claude/rules/ 해당 파일에 규칙 추가
         │     (paths: 매칭으로 관련 파일 식별)
         │
         ├─② CLAUDE.md Never Do (범용이면)
         │
         ├─③ memory/feedback_xxx.md 저장
         │
         └─④ MEMORY.md에 [→ rules/xxx.md] 승격 표기
```

### 피드백 종류별 라우팅

| 피드백 유형 | 예시 | 도착지 |
|-------------|------|--------|
| "이거 하지마" | "subprocess 쓰지마" | rules + Never Do |
| "이렇게 해" | "한국어로 출력해" | rules + Always Do |
| "이건 확인받아" | "DDB 스키마 바꿀 때" | Ask First |
| "이게 설계 이유야" | "Agent는 실행 안 해" | docs/design-decisions.md |
| "이거 나중에 해야 해" | "arch_analysis 분리" | docs/TODO.md |
| "이 API 이렇게 써" | "assetType 소문자만" | rules (해당 모듈) |

---

## 사용자 일상 사용법

### 앱 관리
```
/run                          앱 시작
/restart                      안전 재시작 (active-runs 확인 포함)
/check-module arch_analysis   모듈 API + 문서 + 변경이력 조회
```

### 피드백 주기 (규칙 자동 반영)
```
"에러날 때 retry 하지마, 그냥 실패시켜"
→ Claude가 rules/routes.md + Never Do에 자동 추가

"시나리오 JSON에 namespace 필드 필수로 넣어"
→ Claude가 rules/scenario.md에 자동 추가
```

### 설계 논의
```
"왜 Agent가 직접 실행 안 하는거야?"
→ Claude가 docs/design-decisions.md DD-001 참조해서 답변

"이번에 캐시를 Redis로 바꾸기로 했어"
→ Claude가 docs/design-decisions.md에 DD-008 추가
```

### 작업 시작
```
"arch_analysis.py 리팩토링 하자"
→ Claude가 docs/TODO.md 확인 → claim → 작업 진행
```

---

## 규칙 파일 작성법

### .claude/rules/ 파일 형식

```markdown
---
paths:
  - services/dashboard/routes_*.py    ← glob 패턴
  - services/dashboard/some_file.py   ← 구체 경로
---

# 제목

- 규칙 1 — 이유 또는 결과
- 규칙 2
- 규칙 3
```

### 규칙 추가 기준

| 추가해야 할 때 | 추가하지 말 때 |
|----------------|----------------|
| Claude가 같은 실수 2번 반복 | 코드에서 명확히 읽히는 것 |
| 사용자가 명시적으로 피드백 | 일반적인 코딩 컨벤션 |
| 프로젝트 고유의 gotcha | 언어/프레임워크 기본 규칙 |

---

## 유지보수

### CLAUDE.md 본체
- **100줄 이하 유지** — 넘으면 rules로 이전
- 행동 지시만 작성, 참조 정보는 docs/로

### rules 파일
- 모듈 추가/삭제 시 paths: 업데이트
- 규칙이 10개 넘으면 파일 분리 검토
- 더 이상 유효하지 않은 규칙 제거

### Memory
- rules로 승격된 memory는 `[→ .claude/rules/xxx.md]` 표기
- stale memory (3개월+)는 현재 코드와 대조 후 업데이트/삭제

### docs/design-decisions.md
- 새 설계 결정마다 DD-NNN 추가
- 폐기된 결정은 ~~취소선~~ + 대체 DD 번호 기록

---

## 검증 체크리스트

새 세션 시작 후 아래 항목으로 시스템 정상 동작 확인:

- [ ] `routes_*.py` 파일 열 때 routes.md 규칙 반영되는지
- [ ] "subprocess.Popen으로 만들어" → 거부하는지 (Never Do)
- [ ] "Blueprint 등록 해제할게" → 확인 요청하는지 (Ask First)
- [ ] `/restart` → active-runs 확인 절차 실행하는지
- [ ] 피드백 후 → rules 파일 + memory 동시 업데이트하는지
- [ ] "왜 이렇게 설계했어?" → design-decisions.md 참조하는지
