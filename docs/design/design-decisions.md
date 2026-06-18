# Design Decisions (ADR 경량판)

설계 결정과 그 이유를 기록. 새 결정 추가 시 번호 순서대로.

---

## DD-001: Agent = Generator, App = Executor

**결정**: Agent는 계획/명령을 생성만 하고, 실제 실행(kubectl, AWS CLI)은 App이 subprocess로 수행
**이유**: Agent에게 클러스터 직접 접근 권한을 주면 보안 경계 붕괴. App이 실행을 제어해야 rollback/audit 가능
**영향**: 시나리오 실행 시 Agent 응답 → App 파싱 → subprocess 실행 → 결과 수집

## DD-002: Single-App Q2 + Boundary Expansion

**결정**: 아키텍처 분석은 단일 앱 기준 Q2 질의 후 경계 노드를 확장하는 방식
**이유**: Multi-app 동시 분석은 Agent 응답 품질 저하 (hallucination 증가). 경계에서 확장하면 정확도 유지
**영향**: arch_analysis.py의 ArchitectureAgentDiscoverer가 이 패턴 구현. multi-app은 fallback only

## DD-003: Daemon Thread (not subprocess) for Workers

**결정**: 장시간 작업(분석, 채팅)은 daemon thread로 실행
**이유**: subprocess.Popen + DDB polling은 과도한 복잡성. Flask와 lifecycle 공유가 로컬 개발에 적합
**제약**: Flask auto-reload 시 thread도 죽음 → 분석 중 파일 편집 금지 규칙의 근거

## DD-004: IaC 기반 인프라 확인 + App API 조회

**결정**: 인프라 상태는 CFn stack outputs / CDK context로 확인, 서비스 상태는 app API로 조회
**이유**: 거짓 fallback/더미 데이터는 문제를 숨기고 프로덕션 장애 유발. 실제 소스만 신뢰
**영향**: endpoint 없으면 에러 표시, 존재하는 척 하지 않음

## DD-005: Asset API 단일 경로 (채팅 fallback 제거)

**결정**: Skill CRUD는 Asset API만 사용, Agent 채팅 기반 fallback 제거
**이유**: 채팅 기반은 응답 파싱 불안정, 재시도 로직 복잡. API는 결정적(deterministic)
**영향**: skill_manager.py의 create_asset/update_asset이 유일한 경로

## DD-006: 3-Layer 캐시 전략

**결정**: Memory(5min) → Disk(.skill-cache/) → DDB
**이유**: API 호출 최소화 + 앱 재시작 시에도 데이터 유지 + 다중 인스턴스 공유
**영향**: TTL별 계층화, 각 레이어 독립적으로 invalidate 가능

## DD-007: DDB Event Stream + SSE Relay

**결정**: Long-running operation은 DDB에 이벤트 저장 + SSE로 클라이언트 전달
**이유**: HTTP request lifecycle과 분리. 브라우저 새로고침해도 진행 상황 유지
**영향**: 모든 분석/시나리오 실행이 이 패턴 사용. arch_worker, scenario pipeline 등
