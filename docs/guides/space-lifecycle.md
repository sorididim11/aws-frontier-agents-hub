# Space 라이프사이클 관리

## 개요

Space는 DevOps Agent가 모니터링/분석/실행할 대상 앱을 정의하는 최상위 엔티티다.
생성부터 설정 변경까지 두 가지 경로(Wizard vs Discover)가 존재하며, 배포 방식에 따라 설정 CRUD 흐름이 분기된다.

---

## 1. 생성 경로

### Path A — Wizard 생성 (managed=True)

```
UI Wizard → validate-step(1~5) → generate-cfn → deploy-cfn → _cfn_create_and_wait
         → stack outputs(space_id, role_arn) → _save_space_metadata(managed=True)
```

| 단계 | 검증 내용 |
|------|-----------|
| Step 1 | Space 이름 중복 (Agent API + DDB) |
| Step 2 | Primary/Secondary 계정 접근 (STS identity) |
| Step 3 | 서비스 상태, GitHub 리포 접근, Splunk 엔드포인트, Private Connection |
| Step 4 | IAM Role 존재 (없으면 자동 생성), EKS 클러스터 |
| Step 5 | 최종 확인 |

**배포 흐름 (`_do_deploy`):**
1. CFn 템플릿 생성 (`api_generate_cfn_internal`)
2. Secondary Stack 배포 (cross-account인 경우): IAM Role + EKS AccessEntry
3. Primary Stack 배포: AgentSpace + Role + Associations
4. Metadata 저장: stack outputs에서 space_id/role_arn 추출 → DDB
5. 실패 시 rollback (primary → secondary → orphan Space 순)

**결과 상태:** `managed=True`, `deploy_method="cloudformation"`, `stack_name="{name}-devops-agent"`

### Path B — Discover + Register (managed=False)

```
api/spaces/discover → 계정 스캔 → 적격성 판정
                   → api/spaces/discover/register → 재검증 → _save_space_metadata(managed=False)
```

**Discovery 스캔:**
- Organizations API로 전체 계정 목록 획득 (mgmt_profile 사용)
- 각 계정에서 DevOps Space + Security Space 검색

**적격성 조건 (DevOps Space):**

| 조건 | 필수 | 설명 |
|------|------|------|
| App 태그 | ✓ | `key in ("app", "application")` 태그 존재 |
| AWS Association | ✓ | `cfg.aws` 또는 `cfg.sourceAws` 설정 |
| GitHub 연동 | △ | 없으면 warning (미등록은 아님) |

**적격성 조건 (Security Space):**

| 조건 | 필수 | 설명 |
|------|------|------|
| GitHub 리포 | ✓ | 연결된 리포 존재 필수 |
| DevOps Space 링크 | ✓ | 연결할 DevOps Space 지정 필수 |

**등록 시 재검증:** App 태그 + AWS Association 존재 재확인 (race condition 방어)

**결과 상태:** `managed=False`, `deploy_method=""` (CFn 없음)

---

## 2. CFn Stack ↔ Space 바인딩

### 감지 로직 (`get_space_cfn_info`)

```python
# datasource_manager.py:396
def get_space_cfn_info(session, space_id):
    # 1. DDB space_metadata에서 deploy_method + stack_name 확인
    # 2. Fallback: "{space_name}-devops-agent" 컨벤션
    return (is_cfn_managed: bool, stack_name: str)
```

### cfn_managed 플래그의 영향

| 항목 | cfn_managed=True | cfn_managed=False |
|------|-----------------|-------------------|
| 설정 변경 | CFn UpdateStack (비동기) | Agent API 직접 호출 (동기) |
| 연동 추가/제거 | 템플릿 재생성 → UpdateStack | sync_associations() |
| 버전 추적 | settings_version vs last_deployed_version | 즉시 반영 |
| 동시성 | stack busy 체크 (IN_PROGRESS 거부) | 제한 없음 |

### CFn Import (unmanaged → managed 전환)

```
POST /api/spaces/{id}/cfn-import
  → list_associations() + get_association() × N → 실제 config 수집
  → _generate_import_template() (DeletionPolicy:Retain, 리터럴 SpaceId)
  → CreateChangeSet(ChangeSetType='IMPORT', ResourcesToImport=[...])
  → ExecuteChangeSet → IMPORT_COMPLETE 대기
  → DDB: deploy_method="cloudformation", stack_name="{name}-devops-agent"
```

- Import = READ handler만 호출, 리소스 무변경
- 템플릿 config은 `get_association()` 결과 그대로 사용 (drift 방지)
- 등록(discover/register) 시 `cfn_import: true` 옵션으로 한 번에 가능

### CFn Disconnect (managed → unmanaged 전환)

```
POST /api/spaces/{id}/cfn-disconnect
  → update_stack (모든 리소스 DeletionPolicy:Retain 설정)
  → delete_stack (리소스 보존됨)
  → DDB: deploy_method=""
```

- 리소스는 AWS에 그대로 남음 (Retain)
- 이후 Direct API로 관리하거나 재 import 가능

### CFn Status 조회

```
GET /api/spaces/{id}/cfn-status
  → {deploy_method, stack_name, stack_status, can_import, can_disconnect}
```

---

## 3. Settings CRUD

### Read (`GET /api/spaces/<space_id>/settings`)

1. DDB `space_metadata` 레코드 조회
2. Association ID backfill (AWS API → DDB)
3. Integration enrichment (name, target_url, private_connection_name)
4. 변경 감지 시 DDB 자동 저장 (레거시 마이그레이션)

### Update (`PUT /api/spaces/<space_id>/settings`)

```
┌─────────────────────────────────┐
│ 1. Stack busy 체크 (cfn_managed) │
│ 2. settings_version++           │
│ 3. deploy_status = "pending"    │
└─────────┬───────────────────────┘
          │
    ┌─────┴─────┐
    ▼           ▼
[CFn Path]   [Direct Path]
    │           │
    │  sync_associations()
    │  → add/update/remove
    │  → deploy_status="synced"
    │           │
 generate_cfn  │
 update_stack  │
 (비동기)      │
    │           │
    ▼           ▼
┌─────────────────────────────────┐
│ 4. DDB metadata 최종 저장       │
│    last_deployed_version 갱신   │
└─────────────────────────────────┘
```

**CFn Path 특수 처리:**
- `DisableRollback=True` (단, UPDATE_FAILED 복구 시 제외)
- Stack busy → HTTP 400 거부
- UPDATE_FAILED 상태 → `delete_stack` 후 재시도

**Direct Path:**
- `sync_associations()`: DDB intent vs AWS actual 비교 → add/update/remove
- Provider별 config 변경 감지 → `update_association_config()`

### Delete — Datasource 단건 삭제 (`DELETE /api/spaces/<space_id>/datasources`)

```
요청: {association_id | service_id}
         │
    ┌────┴────┐
    ▼         ▼
[CFn Path]  [Direct Path]
    │         │
 DDB에서     disassociate_service()
 intent 제거   + DDB에서 제거
    │
 CFn template
 재생성 + update_stack
```

- CFn-managed: `_cfn_datasource_change(remove_sid=...)` → DDB에서 제거 + 템플릿 재생성 + UpdateStack
- Direct API: `remove_association()` → `disassociate_service()` + DDB integrations[] 에서 필터링

### Delete — Space 자체 삭제

**현재 미구현.** Space 단위 삭제 API 엔드포인트가 없다.

수동 삭제 시 필요한 절차:
1. AWS 콘솔/CLI에서 Agent Space 삭제
2. CFn stack 삭제 (managed인 경우): primary + secondary
3. DDB `space-meta-{space_id}` 레코드 삭제
4. Security link 정리 (연결된 DevOps Space의 `security_links[]`에서 제거)

> **TODO:** Space deregister/delete API 구현 필요

---

## 4. Drift 감지 & 복구

### Drift 감지 (`GET /api/spaces/<space_id>/drift`)

DDB intended state vs AWS actual state를 비교:

| 유형 | 의미 |
|------|------|
| `missing_in_aws` | DDB에 있지만 AWS에 없음 (외부 삭제 또는 배포 미완료) |
| `missing_in_ddb` | AWS에 있지만 DDB에 없음 (외부 추가 또는 DDB 누락) |

### 복구 액션

| 엔드포인트 | 동작 | 사용 시점 |
|-----------|------|-----------|
| `POST /sync` | AWS 실제 → DDB 강제 동기화 | 외부에서 추가된 연동을 인식시킬 때 |
| `POST /rollback` | AWS 실제 → DDB (실패 변경 포기) | deploy_status=failed이고 원복할 때 |
| `POST /retry` | DDB intent → AWS 재배포 | 일시적 오류로 배포 실패 시 재시도 |

**Rollback 전제:** `deploy_status != "synced"` (이미 동기 상태면 거부)

**Retry 분기:**
- CFn-managed → DDB intent로 템플릿 재생성 → update_stack
- Direct API → `sync_associations()` 재실행

---

## 5. DDB 스키마 (space_metadata)

```
PK: run_id = "space-meta-{space_id}"
SK: record_type = "space_metadata"

── Core ──
space_id, space_name, app_name
app_tag_key, app_tag_value
account_id, secondary_account_id
role_arn

── 배포 상태 ──
managed: bool
deploy_method: "cloudformation" | "api" | ""
stack_name: str
settings_version: int
last_deployed_version: int
deploy_status: "synced" | "pending" | "failed" | "busy"
deploy_error: str

── 연동 ──
integrations: [{provider, service_id, association_id, repo, ...}]
aws_config: {aws: {...}, sourceAws: {...}}
security_agent_space_id: str
security_links: [{security_space_id, name, linked_at}]

── 기타 ──
eks_cluster_name, resource_tags
created_at, updated_at
```

**Never overwritten by settings update:** `managed`, `created_at`, `deploy_method`

---

## 6. CFn 템플릿 리소스

### Primary Stack

| 리소스 | Type | 용도 |
|--------|------|------|
| DevOpsAgentSpace | AWS::DevOpsAgent::AgentSpace | Agent Space 본체 |
| DevOpsAgentRole | AWS::IAM::Role | aidevops 서비스 assume-role |
| MonitorAssociation | AWS::DevOpsAgent::Association | AWS 계정 모니터 + 태그/리소스 필터 |
| SourceAwsAssociation | AWS::DevOpsAgent::Association | Cross-account AWS (secondary 있을 때만) |
| GitHubAssociation | AWS::DevOpsAgent::Association | GitHub 리포 연결 |
| EksAccessEntry | AWS::EKS::AccessEntry | 같은 계정 EKS 접근 |
| {Provider}{N}Association | AWS::DevOpsAgent::Association | 동적 연동 (GitLab, Splunk 등) |
| PrivateConnAssoc{Name} | AWS::DevOpsAgent::Association | Private Connection |

### Secondary Stack (cross-account)

| 리소스 | Type | 용도 |
|--------|------|------|
| CrossAccountDevOpsAgentRole | AWS::IAM::Role | Primary 계정 서비스가 assume |
| CrossAccountAccessEntry | AWS::EKS::AccessEntry | Secondary 클러스터 접근 |

---

## 7. 연동(Integration) 관리

### 정규화 스키마

```json
{
  "provider": "gitlab | github | mcpserversplunk | slack",
  "service_id": "AWS serviceId",
  "association_id": "AWS associationId (backfill)",
  "repo": "owner/repo",
  "target_url": "endpoint URL",
  "private_connection_name": "PC name (optional)",
  "name": "display name (enriched)"
}
```

### Provider Registry

`PROVIDER_REGISTRY` dict로 확장: CFn key 매핑, multi-instance 지원 여부, 필수 필드 정의.
새 datasource 추가 시 registry에 항목만 추가하면 CRUD/CFn 자동 적용.

### Private Connection

```
deploy-private-connection → CFn(AWS::DevOpsAgent::PrivateConnection)
  → poll CREATE_COMPLETE → extract PC name
  → register CA cert (optional) → TCP connectivity test
```

VPC 내부 연결이므로 socket test는 skip, API 상태만 신뢰.

---

## 8. 버전 & 상태 추적

| 필드 | 의미 |
|------|------|
| settings_version | 매 update 시 +1 (의도한 설정 버전) |
| last_deployed_version | 마지막 성공 배포 버전 |
| deploy_status | synced / pending / failed / busy |
| deploy_error | 실패 시 에러 메시지 |

**Drift 감지:** `settings_version != last_deployed_version` → 배포 미반영 상태

---

## 9. 주요 함수 참조

| 함수 | 파일 | 역할 |
|------|------|------|
| `_save_space_metadata` | routes_space.py:1103 | DDB atomic write |
| `get_space_cfn_info` | datasource_manager.py:396 | CFn 관리 여부 판정 |
| `check_stack_busy` | datasource_manager.py | IN_PROGRESS 체크 |
| `update_deploy_status` | datasource_manager.py | 버전/상태 atomic update |
| `sync_associations` | datasource_manager.py | DDB↔AWS 연동 동기화 |
| `backfill_association_ids` | datasource_manager.py | association_id backfill |
| `api_generate_cfn_internal` | routes_space_deploy.py:653 | CFn 템플릿 생성 |
| `_cfn_create_and_wait` | routes_space_deploy.py:422 | Stack 생성 + 이벤트 폴링 |
| `_do_deploy` | routes_space_deploy.py:487 | 전체 배포 오케스트레이션 |
| `api_spaces_discover` | routes_space_registry.py:77 | 계정 스캔 |
| `api_spaces_discover_register` | routes_space_registry.py:351 | 발견된 Space 등록 |
| `api_space_cfn_import` | routes_space_cfn_import.py:257 | 기존 리소스 CFn Import |
| `api_space_cfn_disconnect` | routes_space_cfn_import.py:361 | CFn 관리 해제 (Retain) |
| `api_space_cfn_status` | routes_space_cfn_import.py:447 | CFn 관리 상태 조회 |
| `_generate_import_template` | routes_space_cfn_import.py:121 | Import용 CFn 템플릿 생성 |
| `update_association_config` | datasource_manager.py | in-place 연동 설정 변경 |

---

## 10. 설계 원칙

1. **Dual-path 유연성:** Wizard(표준화) vs Discover(브라운필드) — 두 경로 모두 동일한 DDB 스키마로 수렴
2. **Version tracking:** 비동기 CFn 배포의 drift를 settings_version/last_deployed_version으로 감지
3. **Association backfill:** DDB = intent의 source-of-truth, AWS = actual state. 매 read/update 시 양방향 동기화
4. **Provider registry:** 새 datasource는 registry dict 항목 추가만으로 CRUD/CFn 자동 적용
5. **Stack-busy guard:** IN_PROGRESS 상태에서 concurrent update 방지
6. **Scope boundary:** App 태그(key+value)가 리소스 범위를 정의하며, topology 분석의 진입점
