# TODO

## 작업 규칙

- 시작 전: 해당 모듈 섹션에서 항목 claim (`→ @branch-name (날짜)` 붙임)
- 진행 중인 항목의 관련 파일은 다른 세션에서 수정 금지
- 완료: `[x]` 체크 + 날짜
- 한 세션이 여러 모듈을 동시 claim하지 않을 것

---

## Space 관리

파일: `routes_space*.py`, `datasource_manager.py` | 문서: `docs/space-lifecycle.md`

- [ ] Space delete/deregister API 구현
- [ ] routes_space.py 분리 (settings CRUD → routes_space_settings.py)

## 시나리오 엔진

파일: `verifier*.py`, `execution_engine.py`, `engine_resolver.py`, `scenario_runner.py`, `failure_modes.py` | 문서: `docs/scenario-engine.md`

- (현재 없음)

## 아키텍처 분석

파일: `arch_analysis.py`, `routes_arch.py` | 문서: `docs/arch-analysis-context.md`

- [ ] arch_analysis.py 분리 (Discoverer / Agent / Recommender / Generator — 2800줄 단일 파일)
- [ ] arch-analysis-context.md 정리 (TODO/작업노트 섹션 제거, 설계 문서만 유지)
- [ ] **Baseline+Delta 점진적 분석** — 매번 전체 재분석 대신 증분 갱신 구조
  - [ ] baseline 요약본 설계: Agent에 전달할 compact topology (node name+kind 목록, 토큰 크기 통제)
  - [ ] `#arch-q2-refresh` 스킬 트리거 + delta 응답 포맷 설계 (added/removed/modified)
  - [ ] delta merge 로직 (routes_arch.py: 반환된 delta를 기존 DDB 레코드에 안전 merge)
  - [ ] UI diff 리뷰: 변경분 시각화 + 승인/거부 후 merge (archGenModal 패턴 활용)

## Expert / AI Provider

파일: `routes_expert.py`, `expert_sidecar/`, `chat_worker.py`, `ai_provider.py`, `providers/` | 문서: `docs/expert-agent.md`

- (현재 없음)

## Security

파일: `routes_security_targets.py`, `routes_security_insights.py` | 문서: `docs/security.md`

- [ ] routes_security_targets.py 분리 고려 (1000줄+ — 생성/연동/매칭 혼재)

## Skill 관리

파일: `routes_skills.py`, `skill_manager.py` | 문서: `docs/skills.md`

- (현재 없음)

## 조사 DAG

파일: `routes_dag.py` | 문서: `docs/investigation-dag.md`

- (현재 없음)

## 인프라 Resolution

파일: `cluster_manager.py`, `account_registry.py`, `topology_provider.py`, `credential_resolver.py` | 문서: `docs/infra-resolution.md`

- (현재 없음)

## 문서 정비

- [x] CLAUDE.md 슬림화: "토폴로지 분석 프로세스" + "Architecture Diagram" → docs/arch-analysis-context.md로 이동 (2026-06-02)
- [ ] frontier-agent-hub-internals.md 슬림화: 섹션 4~6 제거 (개별 문서로 위임 완료)

## 리포 분리 (코드 모듈화 후)

- [ ] **devops-dashboard** 리포: `services/dashboard/` + `docs/` + `skills/`
- [x] **frontier-agent-test-dockercoins** 리포: `services/dockercoins/` + `infrastructure/` (2026-06-02) — https://github.com/sorididim11/frontier-agent-test-dockercoins
- [ ] 분리 시 참조 정리 (dashboard → test-infra 간 경로 참조, config.yaml 스키마 등)
