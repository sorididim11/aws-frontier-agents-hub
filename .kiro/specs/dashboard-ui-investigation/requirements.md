# Requirements Document

## Introduction

DevOps Agent Test Simulator 대시보드의 UI/UX 및 기능 개선 작업입니다.
현재 Flask + Jinja2 기반 대시보드에 세 가지 핵심 기능을 추가합니다:
(1) 시나리오 실행 시 해당 알람 이름으로 Slack investigation thread를 자동 연결,
(2) 시나리오 상세 페이지를 3섹션 레이아웃으로 개선,
(3) Bedrock Claude를 활용한 Slack 메시지 한국어 번역 및 AI 분석.

## Glossary

- **Dashboard**: Flask + Jinja2 기반 DevOps Agent Test Simulator 웹 앱 (`services/dashboard/`)
- **Scenario**: 장애 시뮬레이션 시나리오 JSON 파일 (`services/dashboard/scenarios/*.json`)
- **Run**: 시나리오 실행 인스턴스 (SimulationRun 객체)
- **Verifier**: `verifier.py`의 검증 엔진
- **CW_Alarm_Step**: `verification.steps`에서 `type: "cw_alarm"`인 검증 단계
- **Alarm_Name**: CW_Alarm_Step의 `alarm` 필드 값 (예: `devops-agent-test-hasher-errors`)
- **Investigation_Thread**: Slack에서 `Investigation started: [CW Alarm] {alarm-name}` 텍스트를 가진 parent 메시지의 thread replies
- **Slack_API**: Slack Web API (`conversations.history`, `conversations.replies`)
- **Bedrock**: AWS Bedrock Runtime 서비스 (`bedrock-runtime`)
- **Claude**: Bedrock에서 사용하는 `anthropic.claude-3-5-sonnet-20241022-v2:0` 모델
- **Message_Type**: Slack 메시지 텍스트에서 추출한 분류 (Finding, Observation, Investigation, Complete)
- **Scenario_Detail_Page**: 시나리오 카드 클릭 시 표시되는 상세 페이지 (현재 mainView 숨기고 scenarioPage 표시하는 방식)

---

## Requirements

### Requirement 1: 알람 이름 기반 Slack Investigation Thread 조회

**User Story:** As a 대시보드 사용자, I want 시나리오와 연관된 Slack investigation thread만 조회하고 싶다, so that 전체 채널 메시지가 아닌 해당 시나리오의 조사 내용만 집중해서 볼 수 있다.

#### Acceptance Criteria

1. THE Verifier SHALL `get_slack_messages()` 함수에 `alarm_name` 파라미터를 추가하여 알람 이름 기반 필터링을 지원한다.
2. WHEN `alarm_name` 파라미터가 제공되면, THE Verifier SHALL `conversations.history`에서 텍스트에 `Investigation started` 및 해당 `alarm_name`을 포함하는 parent 메시지(`is_thread_reply=False`)를 검색한다.
3. WHEN 해당 parent 메시지가 발견되면, THE Verifier SHALL 해당 메시지의 thread replies만 반환한다 (`is_thread_reply=True`인 메시지들).
4. WHEN `alarm_name` 파라미터가 제공되지 않으면, THE Verifier SHALL 기존 동작(전체 채널 메시지 반환)을 유지한다.
5. IF Slack API 호출이 실패하면, THE Verifier SHALL `{"ok": False, "error": "<에러 메시지>", "messages": []}` 형태로 반환한다.
6. IF 해당 `alarm_name`에 매칭되는 parent 메시지가 없으면, THE Verifier SHALL `{"ok": True, "messages": [], "alarm_name": "<alarm_name>"}` 형태로 반환한다.
7. THE Dashboard SHALL `/api/slack/messages` 엔드포인트에 `alarm_name` 쿼리 파라미터를 추가하여 Verifier의 새 파라미터로 전달한다.
8. WHEN 시나리오에 `cw_alarm` 타입 step이 존재하면, THE Dashboard SHALL 해당 step의 `alarm` 필드 값을 `alarm_name`으로 사용하여 Slack 메시지를 조회한다.

---

### Requirement 2: 시나리오 상세 페이지 3섹션 레이아웃

**User Story:** As a 대시보드 사용자, I want 시나리오 상세 정보를 구조화된 3섹션 레이아웃으로 보고 싶다, so that 시나리오 상태, 조사 메시지, 실행 이력을 한 페이지에서 명확하게 파악할 수 있다.

#### Acceptance Criteria

1. WHEN 사용자가 시나리오 카드를 클릭하면, THE Dashboard SHALL mainView를 숨기고 Scenario_Detail_Page를 표시한다 (현재 방식 유지).
2. THE Scenario_Detail_Page SHALL 다음 3개 섹션을 수직으로 배치한다: 섹션1(시나리오 상세 + 실행 상태), 섹션2(조사 메시지), 섹션3(실행 이력).
3. THE Scenario_Detail_Page의 섹션1 SHALL 시나리오 이름, 목적, 예상 근본 원인, 트리거 버튼, 검증 단계 타임라인을 포함한다.
4. THE Scenario_Detail_Page의 섹션2 SHALL Slack investigation thread 메시지 목록을 표시하며, 각 메시지에 Message_Type 아이콘과 한국어 번역을 함께 표시한다.
5. THE Scenario_Detail_Page의 섹션3 SHALL 해당 시나리오의 과거 실행 이력을 최신순으로 표시한다.
6. THE Scenario_Detail_Page의 최하단 SHALL AI 분석 에이전트 섹션을 배치하며, Bedrock Claude로 조사 결과를 평가하는 버튼과 결과 표시 영역을 포함한다.
7. THE Scenario_Detail_Page SHALL 뒤로 가기 버튼을 제공하여 mainView로 돌아갈 수 있도록 한다.
8. WHILE 시나리오 Run이 진행 중이면, THE Scenario_Detail_Page의 섹션1 SHALL 검증 단계 타임라인을 실시간으로 업데이트한다 (폴링 간격 5초).

---

### Requirement 3: Slack 메시지 한국어 번역

**User Story:** As a 대시보드 사용자, I want 영어로 된 Slack 조사 메시지를 한국어로 번역하여 보고 싶다, so that 영어에 익숙하지 않은 팀원도 조사 내용을 빠르게 이해할 수 있다.

#### Acceptance Criteria

1. THE Dashboard SHALL `/api/translate` POST 엔드포인트를 제공하며, 요청 바디에 `{"text": "<영어 텍스트>"}` 형태를 받는다.
2. WHEN `/api/translate` 요청이 수신되면, THE Dashboard SHALL boto3 `bedrock-runtime` 클라이언트를 사용하여 Claude (`anthropic.claude-3-5-sonnet-20241022-v2:0`)에 번역을 요청한다.
3. THE Dashboard SHALL 번역 결과를 `{"translated": "<한국어 텍스트>"}` 형태로 반환한다.
4. IF Bedrock API 호출이 실패하면, THE Dashboard SHALL `{"translated": null, "error": "<에러 메시지>"}` 형태로 반환하며 HTTP 500을 응답한다.
5. THE Scenario_Detail_Page의 섹션2 SHALL 각 Slack 메시지에 대해 원문(영어)과 번역문(한국어)을 함께 표시한다.
6. THE Scenario_Detail_Page의 섹션2 SHALL 메시지 텍스트에서 Message_Type을 추출하여 다음 아이콘을 표시한다: 🔍 Finding, 📊 Observation, 🔬 Investigation, ✅ Complete.
7. WHEN Message_Type이 위 4가지에 해당하지 않으면, THE Scenario_Detail_Page SHALL 💬 아이콘을 기본값으로 표시한다.
8. THE Dashboard SHALL 번역 요청을 메시지 단위로 처리하며, 동일 텍스트에 대한 중복 번역 요청을 프론트엔드에서 캐싱하여 방지한다.

---

### Requirement 4: AI 분석 에이전트 (Bedrock Claude 연동)

**User Story:** As a 대시보드 사용자, I want AI가 조사 결과를 분석하여 평가해주기를 원한다, so that 에이전트의 조사 품질을 객관적으로 검토할 수 있다.

#### Acceptance Criteria

1. THE Dashboard SHALL `/api/analyze/<run_id>` POST 엔드포인트를 Bedrock Claude와 실제 연동하여 구현한다.
2. WHEN `/api/analyze/<run_id>` 요청이 수신되면, THE Dashboard SHALL 해당 Run의 검증 단계 결과와 Slack investigation thread 메시지를 컨텍스트로 Claude에 전달한다.
3. THE Dashboard SHALL Claude에게 다음을 평가하도록 요청한다: 에이전트가 올바른 근본 원인을 식별했는지, 조사 과정이 체계적인지, 개선 사항이 있는지.
4. THE Dashboard SHALL 분석 결과를 `{"run_id": "<id>", "summary": "<분석 내용>", "status": "completed"}` 형태로 반환한다.
5. IF Run이 존재하지 않으면, THE Dashboard SHALL HTTP 404를 반환한다.
6. IF Bedrock API 호출이 실패하면, THE Dashboard SHALL `{"status": "error", "error": "<에러 메시지>"}` 형태로 HTTP 500을 반환한다.
7. THE Scenario_Detail_Page의 AI 분석 섹션 SHALL 분석 결과를 마크다운 형식으로 렌더링하여 표시한다.
