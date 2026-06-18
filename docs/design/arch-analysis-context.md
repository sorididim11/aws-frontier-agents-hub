# 아키텍처 분석 기능 — 컨텍스트 문서

## 개요

Dashboard "아키텍처 분석" 탭. Agent Space 인프라를 L1→L2→L3 레이어별 자동 분석.
Bedrock converse API → Agent Space 질의 → 그래프(노드/엣지) + 설명 + 보안분석 생성.

### 주요 파일
| 파일 | 역할 |
|------|------|
| `services/dashboard/overview_app.py` | Flask API, DynamoDB CRUD, SSE 스트리밍 |
| `services/dashboard/arch_analysis.py` | 분석 엔진 (`ArchitectureAgentDiscoverer`, `ArchitectAgent`, `AnalysisResult`) |
| `services/dashboard/static/js/arch_topo.js` | 토폴로지 시각화, L1/L2/L3 렌더링 |
| `services/dashboard/templates/space.html` | HTML 템플릿 |

### DynamoDB 레코드
```
테이블: devops-agent-test-scenario-runs (HASH: run_id, RANGE: record_type, GSI: scenario_id + run_id)

분석 결과:  run_id="arch-YYYYMMDD-HHMMSS", record_type="arch_analysis", scenario_id=<space_id>
체크포인트: run_id="arch-cp-<space_id>",   record_type="arch_checkpoint", scenario_id=<space_id>
```

### 시각화 레벨과 데이터 흐름
```
ARCH.nodes / ARCH.edges = 단일 배열, L1→L2→L3가 누적 추가

L1 완료: 서비스 노드(group 할당) + 서비스간 엣지 → _archDataL1()이 group별 앱 노드로 집계
L2 완료: 기존 노드에 kind/namespace 보강 + managed_services 노드/엣지 추가 → _archDataL2()가 앱 내부 + 인프라 분리
L3 완료: external_deps + spof + blast_radius 추가 → _archDataL3()가 서비스 중심 상세 뷰

프론트엔드 뷰:
  L1 (앱 클릭 전) = group별 박스, managed 노드 필터링
  L2 (앱 클릭)    = 해당 group의 서비스들 + 연결된 managed 노드
  L3 (서비스 클릭) = 해당 서비스 + 연결 노드 + SPOF/blast_radius
```

---

## 현재 문제: 구조적 복잡성

### 근본 원인
**in-memory `_arch_state`가 진실의 원천이고, DynamoDB가 fallback** → 조건 분기 폭발.

```python
# 현재 _arch_state (overview_app.py)
_arch_state = {
    "graph": None,           # ← 불필요. DynamoDB에 있음
    "analysis": None,        # ← 불필요. DynamoDB에 있음
    "recommendations": None, # ← 불필요. DynamoDB에 있음
    "checkpoint": None,      # ← 불필요. DynamoDB에 있음
    "layout": None,
    "status": "idle",        # ✓ 필요 (스레드 상태)
    "current_layer": None,   # ✓ 필요 (진행 중 레이어)
    "error_msg": None,       # ✓ 필요 (에러 메시지)
}
```

### 있어야 하는 구조
```
DynamoDB = 데이터의 유일한 원천 (분석 결과, 체크포인트)
in-memory = 스레드 상태만 (running/idle, current_layer, error_msg)
SSE = 분석 중 실시간 데이터를 브라우저에 직접 푸시
```

### 단순화된 API 동작
```
GET /api/arch/status   → { is_running, current_layer, error } + DDB 조회 { has_analysis, has_checkpoint }
GET /api/arch/topology → DDB에서 가져옴. 끝. in-memory cache 없음.
```

---

## TODO

### 1. `_arch_state` 단순화 (최우선)
- `analysis`, `graph`, `checkpoint`, `recommendations` 제거
- `status`, `current_layer`, `error_msg`만 유지
- `api_arch_topology` → DynamoDB 직접 조회 (in-memory fallback 제거)
- `api_arch_status` → `is_running` + DynamoDB 2개 쿼리
- 분석 완료 시 → DynamoDB 저장만. in-memory 업데이트 없음.

### 2. L1 노드 분류(그룹핑) 문제
Sonnet이 group 필드를 잘못 할당. NONE 그룹에 10개 핵심 노드.
- DevOps Agent가 핵심인데 미분류
- Observability가 DevOps Agent 하위인데 별도 최상위 그룹
- `arch_analysis.py` L1/L2 프롬프트 개선 필요

### 3. 에이전트 인터뷰 데이터
`conversations` 캡처 코드 추가됨. 다음 분석부터 자동 저장. 기존 데이터 복구 불가.

### 4. Flask reloader 문제
파일 편집 → reloader → 분석 스레드 kill. 체크포인트로 재개 가능.
production: `--no-reload` 또는 gunicorn.

---

## 실행
```bash
cd services/dashboard && AWS_PROFILE=member1-acc python overview_app.py  # 포트 5003
aws sso login --profile member1-acc  # SSO 만료 시
```

## 주의
- 분석 중 `services/dashboard/` 파일 편집 금지 (Flask reloader)
- DynamoDB 빈 문자열 불가 → `_sanitize_ddb()` 필수
- 분석 5-15분 소요
