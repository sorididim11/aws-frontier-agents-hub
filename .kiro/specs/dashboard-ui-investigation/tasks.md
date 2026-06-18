# Implementation Plan: Dashboard UI Investigation

## Overview

Flask 백엔드 확장(verifier.py, app.py) → IAM 권한 추가 → 프론트엔드 개선(index.html) 순서로 구현합니다.
배포는 `kubectl cp` + gunicorn reload 방식으로 이미지 빌드 없이 핫 배포합니다.

## Tasks

- [x] 1. verifier.py - get_slack_messages() alarm_name 파라미터 추가
  - `get_slack_messages(since_ts=None, limit=20, alarm_name=None)` 시그니처로 변경
  - alarm_name 있을 때: `conversations.history` (limit=200) 조회 → `"Investigation started"` + alarm_name 포함 parent 검색 → `conversations.replies`로 thread replies 반환 (parent 제외)
  - alarm_name 없을 때: 기존 동작 유지
  - 에러 처리: `{"ok": False, "error": "...", "messages": []}` 반환
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

  - [ ]* 1.1 Write unit tests for get_slack_messages() alarm_name 모드
    - alarm_name 있고 parent 발견 → thread replies만 반환 검증
    - alarm_name 있지만 parent 없음 → 빈 messages 반환 검증
    - alarm_name 없음 → 기존 동작 유지 검증
    - Slack API 실패 → ok=False 반환 검증
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.6_

  - [ ]* 1.2 Write property test for alarm_name 기반 thread replies 반환
    - **Property 1: alarm_name 기반 thread replies 반환**
    - **Validates: Requirements 1.2, 1.3**

  - [ ]* 1.3 Write property test for alarm_name 없으면 기존 동작 유지
    - **Property 2: alarm_name 없으면 기존 동작 유지**
    - **Validates: Requirements 1.4**

- [x] 2. app.py - /api/slack/messages alarm_name 파라미터 추가
  - `request.args.get("alarm_name", None)` 추출 후 `get_slack_messages(alarm_name=alarm_name)` 전달
  - _Requirements: 1.7_

- [x] 3. app.py - /api/translate 엔드포인트 신규 구현
  - `POST /api/translate` 엔드포인트 추가
  - 요청 바디: `{"text": "..."}`
  - boto3 `bedrock-runtime` 클라이언트로 `anthropic.claude-3-5-sonnet-20241022-v2:0` 호출
  - 성공: `{"translated": "한국어 텍스트"}` 반환
  - 실패: `{"translated": null, "error": "..."}`, HTTP 500 반환
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ]* 3.1 Write unit tests for /api/translate
    - Bedrock 성공 응답 → `{"translated": "..."}` 검증
    - Bedrock 실패 → HTTP 500 + `{"translated": null, "error": "..."}` 검증
    - _Requirements: 3.3, 3.4_

  - [ ]* 3.2 Write property test for 번역 API 응답 형태
    - **Property 7: 번역 API 응답 형태**
    - **Validates: Requirements 3.3**

- [x] 4. app.py - /api/analyze/<run_id> Bedrock 실제 연동
  - Run 조회 → 없으면 HTTP 404
  - 해당 시나리오의 alarm_name 추출 → `get_slack_messages(alarm_name=alarm_name)` 호출
  - Claude 프롬프트 구성: 시나리오 이름, 기대 근본 원인, 검증 단계 결과, Slack 메시지
  - 성공: `{"run_id": "...", "summary": "...", "status": "completed"}` 반환
  - 실패: `{"status": "error", "error": "..."}`, HTTP 500 반환
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 4.1 Write unit tests for /api/analyze/<run_id>
    - Run 없음 → HTTP 404 검증
    - Bedrock 성공 → `{"run_id", "summary", "status": "completed"}` 검증
    - Bedrock 실패 → HTTP 500 검증
    - _Requirements: 4.4, 4.5, 4.6_

  - [ ]* 4.2 Write property test for AI 분석 응답 형태
    - **Property 10: AI 분석 응답 형태**
    - **Validates: Requirements 4.4**

- [ ] 5. Checkpoint - 백엔드 테스트 통과 확인
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. IAM - dashboard 역할에 Bedrock InvokeModel 권한 추가
  - `dashboard.yaml` 또는 CloudFormation 스택에서 IRSA 역할 정책 수정
  - 추가 권한: `bedrock:InvokeModel` on `arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-5-sonnet-20241022-v2:0`
  - _Requirements: 3.2, 4.1_

- [ ] 7. index.html - getAlarmName() 함수 추가
  - `verification.steps`에서 `type === 'cw_alarm'`인 첫 번째 스텝의 `alarm` 필드 반환
  - 해당 스텝 없으면 `null` 반환
  - _Requirements: 1.8_

  - [ ]* 7.1 Write property test for cw_alarm 스텝에서 alarm_name 추출
    - **Property 3: cw_alarm 스텝에서 alarm_name 추출**
    - **Validates: Requirements 1.8**

- [ ] 8. index.html - scenarioPage 3섹션 레이아웃으로 재구성
  - 기존 `sc-columns` 3컬럼 grid를 수직 3섹션으로 변경
  - 섹션1: 시나리오 이름, 목적, 기대 근본 원인, 트리거 버튼, 검증 단계 타임라인 (5초 폴링)
  - 섹션2: Slack investigation thread 메시지 목록 (아이콘 + 원문 + 번역)
  - 섹션3: 해당 시나리오 실행 이력 최신순
  - 최하단: AI 분석 섹션 (버튼 + 마크다운 결과 영역)
  - 뒤로 가기 버튼 유지
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

  - [ ]* 8.1 Write property test for 섹션1 렌더링 필수 정보 포함
    - **Property 4: 섹션1 렌더링 필수 정보 포함**
    - **Validates: Requirements 2.3**

  - [ ]* 8.2 Write property test for 실행 이력 최신순 정렬
    - **Property 5: 실행 이력 최신순 정렬**
    - **Validates: Requirements 2.5**

- [ ] 9. index.html - 번역 캐시 + translateText() 함수 구현
  - `const translationCache = {}` 모듈 레벨 캐시 선언
  - `translateText(text)`: 캐시 HIT 시 즉시 반환, MISS 시 `POST /api/translate` 호출 후 캐시 저장
  - _Requirements: 3.8_

  - [ ]* 9.1 Write property test for 번역 캐시 idempotence
    - **Property 8: 번역 캐시 idempotence**
    - **Validates: Requirements 3.8**

- [ ] 10. index.html - Slack 메시지 렌더링 개선 (아이콘 + 번역)
  - `getMessageTypeIcon(text)` 함수 구현: Finding→🔍, Observation→📊, Investigation→🔬, Complete→✅, 기타→💬
  - `openScenario(s)` 에서 `getAlarmName(s)` 호출 → `alarm_name` 쿼리 파라미터로 `/api/slack/messages` 요청
  - 각 메시지 렌더링 시 아이콘 + 원문 + `translateText()` 번역 표시
  - 번역 실패 시 원문만 표시 (graceful degradation)
  - _Requirements: 1.8, 3.5, 3.6, 3.7_

  - [ ]* 10.1 Write property test for Message_Type 아이콘 매핑
    - **Property 6: Message_Type 아이콘 매핑**
    - **Validates: Requirements 3.6, 3.7**

- [ ] 11. index.html - AI 분석 섹션 실제 연동
  - 분석 버튼 클릭 시 `POST /api/analyze/<run_id>` 호출
  - 응답 `summary` 필드를 마크다운으로 렌더링하여 결과 영역에 표시
  - 로딩 중 버튼 비활성화, 실패 시 에러 메시지 표시 후 버튼 재활성화
  - _Requirements: 4.7_

  - [ ]* 11.1 Write property test for AI 분석 프롬프트 컨텍스트 포함
    - **Property 9: AI 분석 프롬프트 컨텍스트 포함**
    - **Validates: Requirements 4.2, 4.3**

- [ ] 12. Final checkpoint - 전체 테스트 통과 및 핫 배포 준비
  - Ensure all tests pass, ask the user if questions arise.
  - 배포: `kubectl cp services/dashboard/verifier.py <pod>:/app/verifier.py` 등 변경 파일 복사 후 gunicorn reload

## Notes

- `*` 표시 서브태스크는 선택적 테스트 태스크로 MVP 속도를 위해 건너뛸 수 있음
- Property 테스트는 Hypothesis 라이브러리 사용 (`pytest services/dashboard/tests/test_properties.py -v --hypothesis-seed=0`)
- 단위 테스트: `pytest services/dashboard/tests/ -v`
- Bedrock 권한(태스크 6)은 백엔드 구현(태스크 3, 4) 이후 배포 전에 반드시 적용 필요
