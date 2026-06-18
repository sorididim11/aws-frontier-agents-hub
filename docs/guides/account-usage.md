# Account-Profile 매핑 사용법

## 개요

이 앱은 멀티 AWS 계정 환경에서 Agent Space API를 호출한다.
**황금 규칙: devops-agent API 호출은 반드시 Space 소유 계정의 credentials로 수행해야 한다.**

AWS는 `create_chat`, `send_message` 등 호출 시 calling principal의 account와 Space 소유 account를 비교하며, 불일치하면 `AccessDeniedException: Account ID mismatch`를 반환한다.

## 계정 구조

```
┌─────────────────────────────────────────────────────┐
│  config.yaml                                         │
│  aws.profile: "member2-acc"  ← 글로벌 기본 (DDB 등) │
│  aws.account_id: "089..."    ← DDB, CFn 등 소유     │
│                                                      │
│  clusters:                                           │
│    primary:  590... / member1-acc  ← Agent Space 소유│
│    secondary: 089... / member2-acc                   │
└─────────────────────────────────────────────────────┘
```

- **글로벌 AWS_PROFILE** (`aws.profile`): DynamoDB, ResourceGroupsTaggingAPI 등 인프라 리소스 접근용
- **Space 프로필**: Agent Space API 호출 시 사용. Space마다 다를 수 있음

## Space → Profile 결정 흐름

```
_profile_for_space(space_id)
  │
  ├─ DDB space_metadata.profile (명시적 저장값) → 있으면 반환
  │
  ├─ DDB space_metadata.account_id
  │     → AccountRegistry.get(account_id).profile → 있으면 반환
  │
  └─ fallback: AWS_PROFILE (config.yaml의 aws.profile)
```

## ChatWorker 사용법

ChatWorker는 per-profile pool로 관리된다. 올바른 사용 패턴:

```python
from chat_worker import init_worker, get_worker
from app_config import _profile_for_space, AWS_REGION

profile = _profile_for_space(space_id)
init_worker(profile=profile, region=AWS_REGION)
worker = get_worker(profile)
worker.send_raw(space_id=space_id, session_id="", prompt=question)
```

`get_worker()` 인자 없이 호출하면 하위호환으로 동작:
- worker가 1개면 그것을 반환
- 여러 개면 첫 번째를 반환 (비권장)

## 새 코드 작성 시 체크리스트

devops-agent API 호출 코드를 추가할 때:

1. **Space ID 확보**: 호출 대상 Space의 ID를 명확히 알고 있는가?
2. **프로필 결정**: `_profile_for_space(space_id)` 또는 `_session_for_space(meta)`를 사용하는가?
3. **ChatWorker 경유 시**: `init_worker(profile=...) → get_worker(profile)` 패턴을 사용하는가?
4. **직접 client 생성 시**: `_session_for_space(meta).client("devops-agent")` 패턴을 사용하는가?

## 세션 팩토리 함수 요약

| 함수 | 용도 | 위치 |
|------|------|------|
| `_boto_session()` | 글로벌 기본. DDB, 태그 조회 등 | app_config.py |
| `_session_for_space(meta)` | Space 소유 계정 세션 | app_config.py |
| `_session_for_account_id(id)` | 특정 계정 세션 | app_config.py |
| `_session_for_association(assoc)` | Association 계정 세션 | app_config.py |
| `_profile_for_space(space_id)` | Space → 프로필 문자열 | app_config.py |

## 흔한 실수

1. **`_boto_session()`으로 devops-agent client 생성** → Space가 다른 계정이면 mismatch
2. **ChatWorker를 `AWS_PROFILE`로 초기화** → Space 계정과 무관한 프로필 사용
3. **`get_worker()` 인자 없이 호출** → 어떤 프로필의 worker가 반환될지 불확정
