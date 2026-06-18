# Design Document

## Overview

DevOps Agent Test Simulator 대시보드에 세 가지 핵심 기능을 추가하는 설계입니다.

1. **Slack Investigation Thread 연결**: 시나리오의 `cw_alarm` 스텝에서 알람 이름을 추출하여, 해당 알람과 연관된 Slack investigation thread만 필터링하여 표시
2. **시나리오 상세 페이지 3섹션 레이아웃**: 현재 3컬럼 레이아웃을 수직 3섹션으로 재구성하여 정보 밀도와 가독성 향상
3. **Bedrock Claude 연동**: 영→한 번역 API와 AI 분석 API를 실제 구현

### 기술 스택

- **Backend**: Python 3, Flask, boto3 (Bedrock Runtime, Secrets Manager, CloudWatch)
- **Frontend**: Vanilla JS (단일 HTML 파일, 프레임워크 없음)
- **Slack API**: `conversations.history`, `conversations.replies` (urllib 사용, requests 미사용)
- **AI**: AWS Bedrock `anthropic.claude-3-5-sonnet-20241022-v2:0`

---

## Architecture

### 현재 구조

```
Browser (index.html)
    │
    ├── GET /api/slack/messages?since=&limit=
    ├── POST /api/analyze/<run_id>  (placeholder)
    └── 기타 시나리오/실행 API
         │
         └── Flask (app.py)
              └── verifier.py
                   └── Slack API (conversations.history + replies)
```

### 변경 후 구조

```
Browser (index.html)
    │
    ├── GET /api/slack/messages?alarm_name=&limit=   ← alarm_name 파라미터 추가
    ├── POST /api/translate                           ← 신규
    ├── POST /api/analyze/<run_id>                   ← Bedrock 실제 연동
    └── 기타 API (변경 없음)
         │
         └── Flask (app.py)
              ├── verifier.py
              │    └── get_slack_messages(alarm_name=None)  ← 파라미터 추가
              └── Bedrock Runtime (boto3)
                   └── Claude claude-3-5-sonnet-20241022-v2:0
```

### 데이터 흐름: Slack Thread 조회

```
openScenario(s)
    │
    ├── cw_alarm 스텝에서 alarm_name 추출
    │
    └── GET /api/slack/messages?alarm_name={alarm_name}
              │
              └── verifier.get_slack_messages(alarm_name=alarm_name)
                       │
                       ├── conversations.history 전체 조회 (limit=200)
                       ├── "Investigation started" + alarm_name 포함 parent 검색
                       └── 해당 thread_ts로 conversations.replies 조회
                                └── thread replies만 반환 (parent 제외)
```

### 데이터 흐름: 번역

```
Slack 메시지 렌더링
    │
    ├── 캐시 확인 (translationCache[text])
    │    ├── HIT → 캐시된 번역 즉시 표시
    │    └── MISS → POST /api/translate {text}
    │                    │
    │                    └── Bedrock Claude invoke_model
    │                             └── {"translated": "한국어 텍스트"}
    └── 번역 결과 캐시 저장 후 표시
```

---

## Components and Interfaces

### 1. verifier.py: `get_slack_messages()` 확장

```python
def get_slack_messages(since_ts=None, limit=20, alarm_name=None) -> dict:
    """
    alarm_name이 있으면: "Investigation started: [CW Alarm] {alarm_name}" 포함
    parent 메시지를 찾아 해당 thread replies만 반환.
    alarm_name이 없으면: 기존 동작 (전체 채널 메시지).

    Returns:
        {
            "ok": bool,
            "messages": [...],
            "alarm_name": str | None,  # alarm_name 모드일 때 포함
            "error": str               # 에러 시 포함
        }
    """
```

**alarm_name 모드 처리 로직:**
1. `conversations.history` 호출 (limit=200, 최근 메시지 충분히 조회)
2. 각 메시지에서 `is_thread_reply=False` 이고 텍스트에 `"Investigation started"` + `alarm_name` 포함 여부 확인
3. 매칭 parent 발견 시 → `conversations.replies` 호출 (해당 `thread_ts`)
4. replies에서 parent(ts == thread_ts) 제외하고 반환
5. 매칭 없으면 → `{"ok": True, "messages": [], "alarm_name": alarm_name}` 반환

### 2. app.py: 엔드포인트 변경/추가

#### `/api/slack/messages` 수정

```python
@app.route("/api/slack/messages")
def api_slack_messages():
    since = request.args.get("since", None)
    limit = request.args.get("limit", 20, type=int)
    alarm_name = request.args.get("alarm_name", None)
    return jsonify(get_slack_messages(since_ts=since, limit=limit, alarm_name=alarm_name))
```

#### `/api/translate` 신규

```python
@app.route("/api/translate", methods=["POST"])
def api_translate():
    """
    Request:  {"text": "English text"}
    Response: {"translated": "한국어 텍스트"}
    Error:    {"translated": null, "error": "..."}, HTTP 500
    """
```

#### `/api/analyze/<run_id>` 실제 구현

```python
@app.route("/api/analyze/<run_id>", methods=["POST"])
def api_analyze(run_id):
    """
    Run의 검증 결과 + Slack thread 컨텍스트 → Claude 분석
    Response: {"run_id": "...", "summary": "...", "status": "completed"}
    Error:    {"status": "error", "error": "..."}, HTTP 500
    """
```

### 3. index.html: UI 변경

#### alarm_name 추출 로직

```javascript
function getAlarmName(scenario) {
    const steps = scenario?.verification?.steps || [];
    const cwStep = steps.find(s => s.type === 'cw_alarm');
    return cwStep?.alarm || null;
}
```

#### 번역 캐시

```javascript
const translationCache = {};  // { text: translatedText }

async function translateText(text) {
    if (translationCache[text]) return translationCache[text];
    const res = await fetch('/api/translate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text})
    });
    const data = await res.json();
    if (data.translated) translationCache[text] = data.translated;
    return data.translated || null;
}
```

#### Message_Type 아이콘 추출

```javascript
function getMessageTypeIcon(text) {
    if (/\bFinding\b/i.test(text))     return '🔍';
    if (/\bObservation\b/i.test(text)) return '📊';
    if (/\bInvestigation\b/i.test(text)) return '🔬';
    if (/\bComplete\b/i.test(text))    return '✅';
    return '💬';
}
```

#### 3섹션 레이아웃 (수직 배치)

현재 `sc-columns` (3컬럼 grid)를 수직 3섹션으로 변경:

```
┌─────────────────────────────────────────┐
│ 섹션1: 시나리오 상세 + 실행 상태         │
│  - 시나리오 이름, 목적, 기대 근본 원인   │
│  - 트리거 버튼 + 검증 단계 타임라인      │
├─────────────────────────────────────────┤
│ 섹션2: 조사 메시지 (Slack thread)        │
│  - Message_Type 아이콘                   │
│  - 원문(영어) + 번역(한국어)             │
├─────────────────────────────────────────┤
│ 섹션3: 실행 이력                         │
│  - 해당 시나리오 과거 실행 최신순        │
├─────────────────────────────────────────┤
│ AI 분석 섹션 (하단)                      │
│  - 분석 요청 버튼 + 마크다운 결과 표시   │
└─────────────────────────────────────────┘
```

---

## Data Models

### Slack 메시지 응답 (기존 + 확장)

```python
# 기존 메시지 객체 (변경 없음)
{
    "text": str,
    "ts": str,           # Unix timestamp (float string)
    "user": str,
    "bot_id": str,
    "is_thread_reply": bool
}

# get_slack_messages() 반환값 (alarm_name 모드)
{
    "ok": True,
    "messages": [메시지 객체, ...],  # thread replies만 (parent 제외)
    "alarm_name": str
}

# get_slack_messages() 반환값 (기존 모드)
{
    "ok": True,
    "messages": [메시지 객체, ...]
}

# 에러 반환값
{
    "ok": False,
    "error": str,
    "messages": []
}
```

### 번역 API

```python
# Request
{"text": str}

# Response (성공)
{"translated": str}

# Response (실패)
{"translated": None, "error": str}  # HTTP 500
```

### AI 분석 API

```python
# Response (성공)
{
    "run_id": str,
    "summary": str,   # Claude 분석 결과 (마크다운)
    "status": "completed"
}

# Response (실패)
{"status": "error", "error": str}  # HTTP 500

# Response (Run 없음)
{"error": "Run not found"}  # HTTP 404
```

### Claude 프롬프트 구조 (AI 분석)

```
시스템: DevOps 조사 품질 평가자

컨텍스트:
- 시나리오: {scenario_name}
- 기대 근본 원인: {expected_root_cause}
- 검증 단계 결과: {steps JSON}
- Slack 조사 메시지: {thread messages}

평가 항목:
1. 에이전트가 올바른 근본 원인을 식별했는가?
2. 조사 과정이 체계적인가?
3. 개선 사항이 있는가?
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*


### Property 1: alarm_name 기반 thread replies 반환

*For any* alarm_name 문자열과 Slack 채널 메시지 목록에서, `get_slack_messages(alarm_name=alarm_name)` 호출 시 반환된 모든 메시지는 해당 alarm_name을 포함하는 "Investigation started" parent 메시지의 thread replies여야 하며, `is_thread_reply=True`이어야 한다.

**Validates: Requirements 1.2, 1.3**

### Property 2: alarm_name 없으면 기존 동작 유지

*For any* `since_ts`와 `limit` 값에 대해, `get_slack_messages(alarm_name=None)`의 반환값은 기존 `get_slack_messages()` 동작과 동일한 구조(`ok`, `messages` 포함)를 가져야 한다.

**Validates: Requirements 1.4**

### Property 3: cw_alarm 스텝에서 alarm_name 추출

*For any* 시나리오 JSON에서, `verification.steps` 중 `type === 'cw_alarm'`인 스텝이 존재하면 `getAlarmName(scenario)`는 해당 스텝의 `alarm` 필드 값을 반환해야 하고, 해당 스텝이 없으면 `null`을 반환해야 한다.

**Validates: Requirements 1.8**

### Property 4: 섹션1 렌더링 필수 정보 포함

*For any* 시나리오 데이터에 대해, 섹션1 렌더링 결과는 시나리오 이름(`name`), 목적(`purpose`), 기대 근본 원인(`expected_root_cause`), 트리거 버튼을 포함해야 한다.

**Validates: Requirements 2.3**

### Property 5: 실행 이력 최신순 정렬

*For any* 실행 이력 목록에 대해, `/api/history/<scenario_id>` 응답의 항목들은 `started_at` 기준 내림차순(최신순)으로 정렬되어야 한다.

**Validates: Requirements 2.5**

### Property 6: Message_Type 아이콘 매핑

*For any* 메시지 텍스트에 대해, `getMessageTypeIcon(text)` 함수는 텍스트에 "Finding"이 포함되면 🔍, "Observation"이 포함되면 📊, "Investigation"이 포함되면 🔬, "Complete"이 포함되면 ✅, 그 외에는 💬를 반환해야 한다.

**Validates: Requirements 3.6, 3.7**

### Property 7: 번역 API 응답 형태

*For any* 비어있지 않은 텍스트에 대해, `/api/translate` POST 요청의 성공 응답은 반드시 `translated` 키를 포함하는 JSON 객체여야 한다.

**Validates: Requirements 3.3**

### Property 8: 번역 캐시 idempotence

*For any* 텍스트에 대해, 동일한 텍스트로 `translateText(text)`를 두 번 호출하면 두 번째 호출은 API 요청 없이 첫 번째와 동일한 결과를 반환해야 한다.

**Validates: Requirements 3.8**

### Property 9: AI 분석 프롬프트 컨텍스트 포함

*For any* run 데이터와 Slack thread 메시지 목록에 대해, Claude에 전달되는 프롬프트는 시나리오 이름, 기대 근본 원인, 검증 단계 결과, Slack 메시지를 모두 포함해야 한다.

**Validates: Requirements 4.2, 4.3**

### Property 10: AI 분석 응답 형태

*For any* 유효한 run_id에 대해, `/api/analyze/<run_id>` POST 요청의 성공 응답은 `run_id`, `summary`, `status: "completed"` 키를 모두 포함해야 한다.

**Validates: Requirements 4.4**

---

## Error Handling

### Slack API 에러

| 상황 | 처리 방법 |
|------|-----------|
| Slack 설정 없음 (토큰/채널 미설정) | `{"ok": False, "error": "Slack 설정 없음", "messages": []}` 반환 |
| `conversations.history` API 실패 | `{"ok": False, "error": "<Slack 에러 코드>", "messages": []}` 반환 |
| `conversations.replies` API 실패 | thread fetch 실패는 무시, 빈 replies로 처리 (기존 동작 유지) |
| alarm_name 매칭 parent 없음 | `{"ok": True, "messages": [], "alarm_name": alarm_name}` 반환 |

### Bedrock API 에러

| 상황 | 처리 방법 |
|------|-----------|
| `/api/translate` Bedrock 호출 실패 | `{"translated": null, "error": "<에러 메시지>"}`, HTTP 500 |
| `/api/analyze` Bedrock 호출 실패 | `{"status": "error", "error": "<에러 메시지>"}`, HTTP 500 |
| `/api/analyze` Run 없음 | `{"error": "Run not found"}`, HTTP 404 |
| Bedrock 권한 없음 (IAM) | 에러 메시지에 포함하여 반환, 로그 출력 |

### 프론트엔드 에러

| 상황 | 처리 방법 |
|------|-----------|
| Slack 메시지 로딩 실패 | "Slack 연결 실패: {error}" 메시지 표시 |
| 번역 API 실패 | 원문만 표시 (번역 없이 graceful degradation) |
| AI 분석 실패 | "분석 실패: {error}" 메시지 표시, 버튼 재활성화 |

### Bedrock 권한 설정

현재 IRSA로 AWS 권한이 있으나 Bedrock 권한이 없을 수 있음. 필요한 IAM 정책:

```json
{
    "Effect": "Allow",
    "Action": ["bedrock:InvokeModel"],
    "Resource": "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-5-sonnet-20241022-v2:0"
}
```

---

## Testing Strategy

### 이중 테스트 접근법

단위 테스트와 property 기반 테스트를 함께 사용합니다. 단위 테스트는 구체적인 예시와 에러 케이스를, property 테스트는 임의의 입력에 대한 보편적 속성을 검증합니다.

### 단위 테스트 (pytest)

**verifier.py 테스트:**

```python
# test_verifier.py

def test_get_slack_messages_no_alarm_name():
    """alarm_name 없으면 기존 동작 (conversations.history 직접 반환)"""

def test_get_slack_messages_with_alarm_name_found():
    """alarm_name 있고 parent 발견 시 thread replies만 반환"""
    # Mock: conversations.history → parent 메시지 포함
    # Mock: conversations.replies → thread replies
    # Assert: 반환 messages가 모두 is_thread_reply=True

def test_get_slack_messages_with_alarm_name_not_found():
    """alarm_name 있지만 매칭 parent 없으면 빈 messages 반환"""
    # Assert: {"ok": True, "messages": [], "alarm_name": alarm_name}

def test_get_slack_messages_slack_api_error():
    """Slack API 실패 시 ok=False 반환"""
    # Assert: {"ok": False, "error": "...", "messages": []}
```

**app.py 테스트:**

```python
# test_app.py

def test_translate_endpoint_success():
    """POST /api/translate 성공 케이스"""
    # Mock Bedrock 응답
    # Assert: {"translated": "한국어 텍스트"}

def test_translate_endpoint_bedrock_failure():
    """POST /api/translate Bedrock 실패 시 HTTP 500"""

def test_analyze_endpoint_run_not_found():
    """POST /api/analyze/<run_id> Run 없으면 HTTP 404"""

def test_analyze_endpoint_success():
    """POST /api/analyze/<run_id> 성공 케이스"""
    # Mock: get_active_run, get_slack_messages, Bedrock
    # Assert: {"run_id": ..., "summary": ..., "status": "completed"}
```

### Property 기반 테스트 (Hypothesis)

Python property 기반 테스트 라이브러리로 **Hypothesis**를 사용합니다. 각 테스트는 최소 100회 이상 실행됩니다.

```python
# test_properties.py
from hypothesis import given, settings
from hypothesis import strategies as st

# Feature: dashboard-ui-investigation, Property 3: cw_alarm 스텝에서 alarm_name 추출
@given(st.lists(st.fixed_dictionaries({
    "type": st.sampled_from(["cw_alarm", "pod_logs", "xray_trace", "manual"]),
    "alarm": st.text(min_size=1)
})))
@settings(max_examples=100)
def test_get_alarm_name_from_steps(steps):
    """For any scenario steps, getAlarmName returns the alarm field of the first cw_alarm step"""
    # cw_alarm 스텝이 있으면 해당 alarm 반환, 없으면 None

# Feature: dashboard-ui-investigation, Property 6: Message_Type 아이콘 매핑
@given(st.text())
@settings(max_examples=100)
def test_message_type_icon_mapping(text):
    """For any text, getMessageTypeIcon returns correct icon based on keyword"""
    icon = get_message_type_icon(text)
    if re.search(r'\bFinding\b', text, re.IGNORECASE):
        assert icon == '🔍'
    elif re.search(r'\bObservation\b', text, re.IGNORECASE):
        assert icon == '📊'
    elif re.search(r'\bInvestigation\b', text, re.IGNORECASE):
        assert icon == '🔬'
    elif re.search(r'\bComplete\b', text, re.IGNORECASE):
        assert icon == '✅'
    else:
        assert icon == '💬'

# Feature: dashboard-ui-investigation, Property 5: 실행 이력 최신순 정렬
@given(st.lists(st.fixed_dictionaries({
    "run_id": st.text(min_size=1),
    "started_at": st.datetimes().map(lambda d: d.isoformat()),
    "scenario_id": st.text(min_size=1)
}), min_size=1))
@settings(max_examples=100)
def test_history_sorted_by_started_at_desc(history_items):
    """For any history list, items are sorted by started_at descending"""
    sorted_items = sort_history_desc(history_items)
    for i in range(len(sorted_items) - 1):
        assert sorted_items[i]["started_at"] >= sorted_items[i+1]["started_at"]

# Feature: dashboard-ui-investigation, Property 8: 번역 캐시 idempotence
@given(st.text(min_size=1))
@settings(max_examples=100)
def test_translation_cache_idempotence(text):
    """For any text, second translateText call returns same result without API call"""
    cache = {}
    call_count = 0
    def mock_api(t):
        nonlocal call_count
        call_count += 1
        return "번역: " + t
    result1 = translate_with_cache(text, cache, mock_api)
    result2 = translate_with_cache(text, cache, mock_api)
    assert result1 == result2
    assert call_count == 1  # API는 한 번만 호출

# Feature: dashboard-ui-investigation, Property 1: alarm_name 기반 thread replies 반환
@given(
    st.text(min_size=1, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))),
    st.lists(st.fixed_dictionaries({
        "text": st.text(),
        "ts": st.text(min_size=1),
        "reply_count": st.integers(min_value=0, max_value=10)
    }))
)
@settings(max_examples=100)
def test_alarm_name_filter_returns_only_thread_replies(alarm_name, messages):
    """For any alarm_name and message list, returned messages are all thread replies"""
    # Mock Slack API with controlled data
    result = get_slack_messages_with_mock(alarm_name=alarm_name, mock_messages=messages)
    if result["ok"] and result["messages"]:
        assert all(m["is_thread_reply"] for m in result["messages"])
```

### 테스트 실행

```bash
# 단위 테스트
pytest services/dashboard/tests/ -v

# Property 테스트 (단일 실행)
pytest services/dashboard/tests/test_properties.py -v --hypothesis-seed=0
```

### 통합 테스트 (수동)

Bedrock 연동은 실제 AWS 환경에서 수동으로 검증:

1. `/api/translate` 호출 → 한국어 번역 확인
2. 시나리오 실행 후 `/api/analyze/<run_id>` 호출 → Claude 분석 결과 확인
3. alarm_name이 있는 시나리오에서 Slack 섹션 → 해당 thread만 표시 확인
