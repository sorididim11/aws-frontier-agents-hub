# Requirements Document

## Introduction

DevOps Agent가 RCA(Root Cause Analysis) 조사 시 참조한 실제 데이터 소스(CloudWatch 메트릭, X-Ray 트레이스, K8s 이벤트, 코드 스니펫, 로그 등)를 대시보드에서 직접 확인할 수 있는 기능입니다.

현재 대시보드는 조사 결론(예: "메모리 128Mi 초과")만 표시하며, 그 근거가 되는 원본 데이터를 확인하려면 각 AWS 콘솔이나 kubectl을 직접 사용해야 합니다. 이 기능은 조사 메시지에서 데이터 소스 참조를 자동 추출하고, 각 소스에 대한 실제 데이터 또는 직접 링크를 대시보드 내에서 인라인으로 제공합니다.

## Glossary

- **Dashboard**: Flask + Jinja2 기반 DevOps Agent Test Simulator 웹 앱 (`services/dashboard/`)
- **Evidence_Extractor**: 조사 메시지 텍스트에서 데이터 소스 참조를 파싱하여 구조화된 Evidence 객체를 생성하는 백엔드 모듈
- **Evidence**: 조사 결론의 근거가 되는 개별 데이터 소스 참조 (메트릭 값, 트레이스 ID, 코드 위치, K8s 이벤트 등)
- **Evidence_Panel**: 시나리오 상세 페이지에서 Evidence 목록을 소스 유형별로 그룹화하여 표시하는 UI 섹션
- **Evidence_Type**: Evidence의 분류 카테고리 (`CloudWatch`, `K8s`, `Logs`, `Traces`, `Code`, `CloudTrail`)
- **Deep_Link**: 해당 Evidence의 원본 데이터를 AWS 콘솔 또는 외부 도구에서 직접 확인할 수 있는 URL
- **Inline_Preview**: Deep_Link로 이동하지 않고 대시보드 내에서 직접 확인할 수 있는 데이터 미리보기 (코드 스니펫, 메트릭 값, pod describe 결과 등)
- **Journal_Record**: DevOps Agent API (`list_journal_records`)에서 반환하는 조사 기록 항목
- **Classified_Message**: Bedrock을 통해 분류된 조사 메시지 (`_classify_raw_messages` 반환값, `source` 필드 포함)
- **AWS_Region**: 대시보드가 연결된 AWS 리전 (환경변수 `AWS_REGION`, 기본값 `us-east-1`)
- **Scenario**: 장애 시뮬레이션 시나리오 JSON 파일 (`services/dashboard/scenarios/*.json`)

---

## Requirements

### Requirement 1: 조사 메시지에서 Evidence 참조 추출

**User Story:** As a 대시보드 사용자, I want 조사 메시지에서 참조된 데이터 소스가 자동으로 추출되기를 원한다, so that 결론의 근거가 되는 원본 데이터를 일일이 찾지 않아도 된다.

#### Acceptance Criteria

1. WHEN Classified_Message 목록이 생성되면, THE Evidence_Extractor SHALL 각 메시지의 `source` 필드와 텍스트 내용을 분석하여 Evidence 객체 목록을 생성한다.
2. THE Evidence_Extractor SHALL 다음 Evidence_Type을 인식한다: `CloudWatch` (메트릭 이름, 알람 이름, 수치 값), `K8s` (pod 이름, namespace, 이벤트 유형, 재시작 횟수), `Logs` (로그 그룹, 로그 패턴), `Traces` (트레이스 ID, 서비스 이름, 응답 시간), `Code` (파일 경로, 줄 번호, 심볼 이름), `CloudTrail` (API 호출, 변경 이력).
3. THE Evidence_Extractor SHALL 각 Evidence 객체에 다음 필드를 포함한다: `type` (Evidence_Type), `title` (한국어 1줄 요약), `raw_ref` (원본 메시지에서 추출한 참조 텍스트), `source_message_index` (원본 Classified_Message 인덱스).
4. WHEN 동일한 데이터 소스가 여러 메시지에서 참조되면, THE Evidence_Extractor SHALL 중복을 제거하고 하나의 Evidence 객체로 병합한다.
5. IF 메시지 텍스트에서 데이터 소스 참조를 추출할 수 없으면, THE Evidence_Extractor SHALL 해당 메시지를 건너뛰고 추출 가능한 메시지만 처리한다.

---

### Requirement 2: Evidence에 대한 Deep Link 생성

**User Story:** As a 대시보드 사용자, I want 각 Evidence에 대한 AWS 콘솔 직접 링크를 제공받고 싶다, so that 원본 데이터를 한 번의 클릭으로 확인할 수 있다.

#### Acceptance Criteria

1. WHEN Evidence_Type이 `CloudWatch`이면, THE Dashboard SHALL 해당 메트릭의 CloudWatch 콘솔 URL을 생성한다 (형식: `https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#alarmsV2:alarm/{alarm_name}`).
2. WHEN Evidence_Type이 `Traces`이고 트레이스 ID가 존재하면, THE Dashboard SHALL X-Ray 콘솔 URL을 생성한다 (형식: `https://{region}.console.aws.amazon.com/xray/home?region={region}#/traces/{trace_id}`).
3. WHEN Evidence_Type이 `Code`이고 파일 경로가 존재하면, THE Dashboard SHALL GitHub 파일 URL을 생성한다 (형식: `https://github.com/{repo}/blob/main/{filepath}#L{line_start}-L{line_end}`).
4. WHEN Evidence_Type이 `Logs`이고 로그 그룹이 존재하면, THE Dashboard SHALL CloudWatch Logs 콘솔 URL을 생성한다.
5. WHEN Evidence_Type이 `K8s`이면, THE Dashboard SHALL Deep_Link 대신 `kubectl` 명령어 텍스트를 제공한다 (예: `kubectl describe pod -n dockercoins -l app=hasher`).
6. IF Deep_Link 생성에 필요한 정보가 부족하면, THE Dashboard SHALL Deep_Link 없이 Evidence 제목과 원본 참조 텍스트만 표시한다.
7. THE Dashboard SHALL Deep_Link 생성 시 AWS_Region 환경변수 값을 사용한다.

---

### Requirement 3: Evidence Inline Preview (실제 데이터 조회)

**User Story:** As a 대시보드 사용자, I want 대시보드를 떠나지 않고 Evidence의 실제 데이터를 미리보기로 확인하고 싶다, so that AWS 콘솔을 왔다 갔다 하지 않고 조사 근거를 빠르게 검증할 수 있다.

#### Acceptance Criteria

1. WHEN Evidence_Type이 `Code`이고 사용자가 미리보기를 요청하면, THE Dashboard SHALL `/api/code/{filepath}` 엔드포인트를 호출하여 해당 줄 범위의 코드 스니펫을 Inline_Preview로 표시한다.
2. WHEN Evidence_Type이 `K8s`이고 사용자가 미리보기를 요청하면, THE Dashboard SHALL 새로운 `/api/evidence/k8s` 엔드포인트를 통해 `kubectl describe pod` 또는 `kubectl get events` 결과를 Inline_Preview로 표시한다.
3. WHEN Evidence_Type이 `CloudWatch`이고 알람 이름이 존재하면, THE Dashboard SHALL 새로운 `/api/evidence/cloudwatch` 엔드포인트를 통해 해당 알람의 현재 상태와 최근 메트릭 데이터포인트를 Inline_Preview로 표시한다.
4. WHEN Evidence_Type이 `Traces`이고 트레이스 ID가 존재하면, THE Dashboard SHALL 새로운 `/api/evidence/xray` 엔드포인트를 통해 해당 트레이스의 요약 정보(서비스, 응답 시간, 오류 여부)를 Inline_Preview로 표시한다.
5. WHEN Evidence_Type이 `Logs`이면, THE Dashboard SHALL 새로운 `/api/evidence/logs` 엔드포인트를 통해 해당 로그 그룹의 최근 매칭 로그 이벤트를 Inline_Preview로 표시한다.
6. IF Inline_Preview 데이터 조회가 실패하면, THE Dashboard SHALL 오류 메시지와 함께 Deep_Link를 대안으로 제공한다.
7. THE Dashboard SHALL Inline_Preview 데이터를 접을 수 있는(collapsible) 형태로 표시하여 화면 공간을 절약한다.

---

### Requirement 4: Evidence Panel UI

**User Story:** As a 대시보드 사용자, I want 조사에서 참조된 모든 Evidence를 한 곳에서 유형별로 정리하여 보고 싶다, so that 조사 근거를 체계적으로 검토할 수 있다.

#### Acceptance Criteria

1. THE Scenario_Detail_Page SHALL 조사 과정 섹션 내에 Evidence_Panel을 배치한다.
2. THE Evidence_Panel SHALL Evidence를 Evidence_Type별로 그룹화하여 표시하며, 각 그룹에 아이콘과 건수를 표시한다 (📊 CloudWatch, ☸️ K8s, 📝 Logs, 🔍 Traces, 💻 Code, 🔐 CloudTrail).
3. WHEN 사용자가 개별 Evidence 항목을 클릭하면, THE Evidence_Panel SHALL 해당 Evidence의 Inline_Preview를 토글(열기/닫기)한다.
4. THE Evidence_Panel SHALL 각 Evidence 항목에 Deep_Link 버튼(🔗)을 표시하며, 클릭 시 새 탭에서 해당 URL을 연다.
5. WHEN 조사가 진행 중이면, THE Evidence_Panel SHALL 새로운 Evidence가 추출될 때마다 목록을 자동 업데이트한다.
6. IF Evidence가 하나도 추출되지 않으면, THE Evidence_Panel SHALL "추출된 Evidence가 없습니다" 메시지를 표시한다.
7. THE Evidence_Panel SHALL 각 Evidence 항목 옆에 해당 Evidence를 참조한 원본 조사 메시지로 스크롤하는 링크를 제공한다.

---

### Requirement 5: Evidence 추출 API 엔드포인트

**User Story:** As a 대시보드 개발자, I want Evidence 추출과 조회를 위한 API 엔드포인트를 제공하고 싶다, so that 프론트엔드에서 Evidence 데이터를 효율적으로 사용할 수 있다.

#### Acceptance Criteria

1. THE Dashboard SHALL `/api/evidence/extract` POST 엔드포인트를 제공하며, 요청 바디에 `task_id`를 받아 해당 조사의 Evidence 목록을 반환한다.
2. WHEN `/api/evidence/extract` 요청이 수신되면, THE Dashboard SHALL 기존 `/api/investigation-journal` 엔드포인트의 Classified_Message 결과를 활용하여 Evidence를 추출한다.
3. THE Dashboard SHALL `/api/evidence/k8s` GET 엔드포인트를 제공하며, `pod` 및 `namespace` 파라미터를 받아 `kubectl describe pod` 결과를 반환한다.
4. THE Dashboard SHALL `/api/evidence/cloudwatch` GET 엔드포인트를 제공하며, `alarm_name` 파라미터를 받아 알람 상태와 최근 메트릭 데이터포인트를 반환한다.
5. THE Dashboard SHALL `/api/evidence/xray` GET 엔드포인트를 제공하며, `trace_id` 파라미터를 받아 트레이스 요약 정보를 반환한다.
6. THE Dashboard SHALL `/api/evidence/logs` GET 엔드포인트를 제공하며, `log_group` 및 `pattern` 파라미터를 받아 최근 매칭 로그 이벤트를 반환한다.
7. IF 필수 파라미터가 누락되면, THE Dashboard SHALL HTTP 400과 함께 누락된 파라미터를 명시하는 오류 메시지를 반환한다.
8. IF AWS API 호출이 실패하면, THE Dashboard SHALL HTTP 500과 함께 `{"ok": false, "error": "<에러 메시지>"}` 형태로 반환한다.

---

### Requirement 6: 기존 메시지 분류와 Evidence 연동

**User Story:** As a 대시보드 사용자, I want 기존 메시지 분류(Symptom/Observation/Finding/Conclusion) 결과와 Evidence가 연결되기를 원한다, so that 어떤 조사 단계에서 어떤 데이터를 참조했는지 맥락을 파악할 수 있다.

#### Acceptance Criteria

1. WHEN `_classify_raw_messages` 함수가 메시지를 분류할 때, THE Evidence_Extractor SHALL 각 Classified_Message의 `source` 필드와 `code_ref` 필드를 활용하여 Evidence를 생성한다.
2. THE Evidence_Panel SHALL 각 Evidence 항목에 해당 Evidence가 속한 조사 단계(Symptom/Observation/Finding/Conclusion)를 색상 태그로 표시한다.
3. WHEN 사용자가 메시지 분류 뷰에서 특정 메시지를 클릭하면, THE Dashboard SHALL 해당 메시지와 연결된 Evidence 항목을 Evidence_Panel에서 하이라이트한다.
4. WHEN 사용자가 Evidence_Panel에서 특정 Evidence를 클릭하면, THE Dashboard SHALL 해당 Evidence를 참조한 메시지를 메시지 분류 뷰에서 하이라이트한다.
