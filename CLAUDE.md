# Frontier Agent Hub

DevOps Agent + Security Agent 통합 대시보드.
진입점: `services/dashboard/overview_app.py` (port 5003)

---

## Quick Commands

```bash
# 앱 실행
cd services/dashboard && python overview_app.py

# 앱 재시작 (안전)
curl -s http://localhost:5003/api/active-runs | python -m json.tool  # 진행 중 작업 확인
# 작업 없으면 Ctrl+C → 재실행

# 특정 모듈 테스트
python -m pytest services/dashboard/tests/ -v

# 린트
ruff check services/dashboard/
```

---

## Architecture (간략)

```
config.yaml → app_config.py
  ├→ cluster_manager (account_registry + topology_provider + credential_resolver)
  ├→ ai_provider (bedrock_direct | agent_space → chat_worker)
  ├→ verifier → multi_agent_engine (Generator + Verifier + Evaluator)
  └→ security_enrichment + findings_store
```

상세: `@docs/module-map.md`

---

## Always Do

- 코드 변경 후 반드시 앱 재시작 (Flask auto-reload가 analysis thread를 kill함)
- 재시작 전 `GET /api/active-runs`로 진행 중 작업 확인
- AI 출력 및 UI 라벨은 **한국어**
- API 사용 전 `dir()`/`help()`로 메서드명 검증 — 절대 추측하지 말 것
- CLAUDE.md 문서 인덱스 먼저 확인, 그 후 git log/grep 탐색
- Agent Space API 호출 시 `_profile_for_space(space_id)` 사용
- 시나리오 커맨드는 클러스터에서 직접 테스트 후 JSON에 반영
- Worker는 daemon thread 패턴 사용 (subprocess.Popen 금지)
- 문서 변경은 코드 변경과 함께 — 불일치 방치 금지

---

## Never Do

- 분석/시나리오 실행 중 파일 편집 (thread 죽음)
- `subprocess.Popen` + DDB polling으로 worker 생성
- 실패 시 silent fallback URL 시도 — 명확한 에러 메시지 출력
- 거짓 fallback(더미 endpoint, 가짜 응답) 생성 — IaC + 실제 app API로 확인할 것
- 글로벌 `AWS_PROFILE`로 Agent Space API 호출
- `SKILL.md` 직접 편집 (build.sh 통해 자동 조립됨)
- Agent에게 kubectl exec/apply 같은 실행 권한 부여 (Agent=generator, App=executor)
- 토폴로지 분석에서 multi-app 강제 bypass
- Bedrock tool_use에서 `inputSchema.required` 필드 누락

---

## Ask First

- 새 FM(장애 모드) 템플릿 추가/삭제
- Blueprint 등록/해제 (overview_app.py 수정)
- DDB 스키마 변경
- Multi-account 설정 변경
- config.yaml 구조 변경
- 신규 외부 API 연동 추가

---

## 설계 결정 & 구현 문제

- 설계 이유가 궁금하면: `docs/design-decisions.md` (DD-001~)
- 모듈별 known issues/gotchas: 해당 `.claude/rules/*.md` 파일 참조
- 새 설계 결정 시: `docs/design-decisions.md`에 DD-NNN 추가 후 작업 진행

---

## TODO 관리

- 작업 시작 전: `docs/TODO.md` 확인 → 해당 모듈 섹션에서 claim
- claim 방식: `→ @branch-name (날짜)` 붙임
- 완료 시: `[x]` 체크 + 날짜 기록
- 새 작업 발견 시: 즉시 TODO에 추가 (나중에 하지 않음)
- 한 세션이 여러 모듈을 동시 claim하지 않을 것

---

## When Stuck

1. `docs/module-map.md` — Blueprint, 의존성, 데이터 흐름 전체 맵
2. `docs/design-decisions.md` — "왜 이렇게 만들었는지" 설계 근거
3. `docs/TODO.md` — 모듈별 작업 현황 (claim 규칙)
4. 특정 모듈 docs: `docs/scenario-engine.md`, `docs/skills.md`, `docs/security.md` 등
5. `grep -r "def " services/dashboard/<module>.py | head -20` — 모듈 API 탐색
6. `git log --oneline -10 -- <file>` — 최근 변경 의도 파악
