# Skill 관리 시스템

## 개요

Skill은 Agent Space에 등록되는 지식 단위(Knowledge Item)이다.
모든 CRUD는 **Asset API** 단일 경로로 동작하며, 로컬 파일시스템에 의존하지 않는다.

---

## 1. 아키텍처

```
Skills 탭 (2-panel)
├── Left: Space Skills (배포된 스킬 목록)
└── Right: Unified Skill Library
    ├── 사용자 정의 (skill_type=USER, 사용자가 생성)
    ├── AWS 카탈로그 (GitHub 225개, 미등록 스킬)
    └── ★ 추천 (토폴로지 기반, 배지로 표시)
    + 검색 바 + 소스 필터 (전체/추천/사용자 정의/AWS 서비스) + 카테고리 필터
```

### 모듈 구조

| 모듈 | 역할 | Source of Truth |
|------|------|-----------------|
| `skill_manager.py` | Space 스킬 CRUD, 캐시 | Asset API (Agent Space) |
| `catalog_manager.py` | AWS 카탈로그 인덱싱, 추천 | GitHub repo (git sparse-clone) |
| `routes_skills.py` | REST API 엔드포인트 | — |

---

## 2. SKILL.md 포맷

```markdown
---
name: my-skill
description: 스킬 설명 (1-3줄)
agent_types:
  - Generic
  - Incident RCA
---

# 스킬 내용

## 핵심 규칙
- Rule 1
```

| 필드 | 필수 | 설명 |
|------|------|------|
| name | ✓ | kebab-case 식별자 |
| description | ✓ | 1-3줄 요약 |
| agent_types | ✓ | [Generic, Incident RCA, INCIDENT_TRIAGE] |

---

## 3. CRUD API

### 핵심 API

| Method | Path | 역할 |
|--------|------|------|
| GET | `/api/skills?space_id=X` | Space 스킬 목록 (캐시 우선) |
| POST | `/api/skills/refresh` | 명시적 새로고침 (blocking) |
| POST | `/api/skills/create` | 스킬 생성 (content 직접 전달) |
| GET | `/api/skills/{id}?space_id=X` | 스킬 내용 조회 |
| PUT | `/api/skills/{id}` | 스킬 수정 (content 직접 전달) |
| DELETE | `/api/skills/{id}?space_id=X` | 스킬 삭제 |
| POST | `/api/skills/toggle` | 활성/비활성 전환 |
| POST | `/api/skills/update-agent-types` | agent_types 변경 |
| POST | `/api/skills/generate` | AI 초안 생성 |

### 사용 예시

```bash
SPACE="eddc5899-3959-4502-90f6-af719fc9b8dc"

# 생성
curl -X POST http://localhost:5003/api/skills/create \
  -H "Content-Type: application/json" \
  -d '{"space_id":"'$SPACE'","content":"---\nname: my-skill\ndescription: test\nagent_types:\n  - Generic\n---\n# Content"}'

# 조회
curl "http://localhost:5003/api/skills/ki-xxx?space_id=$SPACE"

# 수정
curl -X PUT http://localhost:5003/api/skills/ki-xxx \
  -H "Content-Type: application/json" \
  -d '{"space_id":"'$SPACE'","content":"---\nname: my-skill\n..."}'

# 삭제
curl -X DELETE "http://localhost:5003/api/skills/ki-xxx?space_id=$SPACE"
```

---

## 4. AWS Skill Catalog

### 개요

GitHub `aws-samples/sample-ai-agent-skills` 레포의 225개 AWS 서비스 troubleshooting 스킬을 카탈로그로 제공.

### 수집 방식: git sparse-clone

```
git clone --depth 1 --filter=blob:none --sparse <repo>
git sparse-checkout set --no-cone '*/SKILL.md'
```

- **Cold (첫 clone)**: ~11초
- **Warm (git pull)**: ~2초
- **수동 갱신만**: 자동 polling 없음, 설정 화면에서 Refresh 버튼

### 카테고리 (17개)

`SERVICE_CATEGORY_MAP` dict로 폴더명 → 카테고리 매핑:

Containers, Compute, Database, Networking, Storage, Security, Integration, Monitoring, Management, DevOps, AI/ML, Analytics, Workspace, IoT, Migration, Frontend, Other

### 카탈로그 API

| Method | Path | 역할 |
|--------|------|------|
| GET | `/api/skills/catalog` | 인덱스 전체 (캐시) |
| POST | `/api/skills/catalog/refresh` | git pull + 재빌드 |
| GET | `/api/skills/catalog/{folder}` | 스킬 상세 (SKILL.md + refs) |
| POST | `/api/skills/catalog/{folder}/deploy` | GitHub → Agent Space 등록 |

### 카탈로그 스킬 등록 흐름

```
POST /api/skills/catalog/{folder}/deploy
  → GitHub API로 SKILL.md + references 다운로드
  → ZIP 번들 (SKILL.md + references/*.md)
  → create_asset(assetType='skill', content={zip: zipFile})
  → Agent Space 즉시 등록 (1~2초)
```

---

## 5. 토폴로지 기반 추천

arch-discover Q2 분석 결과의 `nodes[].kind` → `KIND_TO_CATALOG` 매핑 → 미등록 스킬만 추천.

| Method | Path | 역할 |
|--------|------|------|
| GET | `/api/skills/recommend?space_id=X` | 추천 목록 |
| POST | `/api/skills/recommend/apply` | 추천 일괄 등록 |

---

## 6. 자동 배포 (Default Skills)

`SkillManager.DEFAULT_SKILLS = ["arch-discover", "k8s-detail"]`

| 트리거 | 위치 |
|--------|------|
| 아키텍처 분석 시작 | `routes_arch.py` |
| Space Register | `routes_space/discover.py` |

`ensure_default_skills(space_id)`:
1. list_remote → 미배포 스킬 확인
2. 로컬 `skills/` 디렉토리에서 읽어 deploy (개발자 워크플로우)

---

## 7. 캐시 전략

| 계층 | 위치 | 수명 |
|------|------|------|
| Memory | `_skills_cache[space_id]` | 앱 프로세스 수명 |
| Disk | `.skill-cache/{space_id}.json` | 앱 재시작 생존 |

- **조회**: Memory → Disk → API (background fetch)
- **Invalidation**: CRUD 성공 → `invalidate_cache()` (non-blocking background refresh)
- **Catalog**: `.skill-cache/catalog.json` (수동 refresh만)

---

## 8. 개발자 워크플로우 (내부 전용)

`skills/` 디렉토리는 개발자가 스킬을 코드처럼 관리하는 공간.
UI 사용자에게는 노출되지 않음.

```
skills/{skill-name}/
├── SKILL.md          ← 배포 대상 (src/ 있으면 자동 생성)
├── src/              ← (옵션) 모듈식 소스 → 정렬 조립 → SKILL.md
└── references/       ← (옵션) 런북 파일
```

### 현재 등록된 스킬 (6개)

| 스킬 | 구조 | agent_types | 용도 |
|------|------|-------------|------|
| `arch-discover` | 모듈형 (src/) | Generic | Q1/Q2 토폴로지 분석 |
| `k8s-detail` | 단일 SKILL.md | Generic | K8s 리소스 상세 수집 |
| `scenario-generate` | 단일 SKILL.md | Generic | 시나리오 JSON 생성 |
| `rca-independent-analysis` | 단일 SKILL.md | Incident RCA, Generic | 독립 RCA 규칙 |
| `rca-code-analysis-reporting` | 단일 SKILL.md | Incident RCA, Generic | 코드 분석 포함 규칙 |
| `triage-independent-investigations` | 단일 SKILL.md | Generic | 독립 트리거 규칙 |

`SkillManager.deploy(space_id, skill_name)`:
1. `src/` 있으면 `*.md` 정렬 조립 → SKILL.md 생성 (Python 내장)
2. `SKILL.md` + 파일들 → ZIP (`_ZIP_EXCLUDE_EXTS`: .sh/.py/.pyc/.zip, `_ZIP_EXCLUDE_DIRS`: src/__pycache__/.git)
3. `create_asset` 또는 `update_asset` 호출

이 경로는 `ensure_default_skills`와 개발자 직접 호출에서만 사용됨.

---

## 9. Asset API 주의사항

| 항목 | 값 |
|------|-----|
| 응답 키 | `resp['items']` (not `assets`) |
| space_id 형식 | UUID (`eddc5899-...`), `sp-` prefix 불가 |
| assetType | `'skill'` (소문자만), `'SKILL'` → ValidationException |
| 메타데이터 | `items[].metadata.{name, status, agent_types, skill_type, description}` |
| skill_type | `USER` (사용자 생성) / `LEARNED` (Agent 학습) |

### 전제조건

- AWS CLI ≥ 2.35.4
- `~/.aws/models/devops-agent/2026-01-01/service-2.json` (CLI 번들에서 복사)
- `gh` CLI 인증 (카탈로그 접근)
- IAM: `aidevops:CreateAsset`, `ListAssets`, `GetAsset`, `UpdateAsset`, `DeleteAsset`
- Profile: `member2-acc` (space owner)

---

## 10. 설계 원칙

1. **API-first**: 모든 CRUD는 Asset API 한 경로. 로컬 파일시스템 의존 없음.
2. **채팅 없음**: 이전 `manage-skills` 채팅 fallback 제거됨. API 실패 시 에러 직접 전파.
3. **수동 갱신**: 카탈로그 자동 polling 없음. 설정 화면에서 수동 Refresh.
4. **Disk 캐시 우선**: 재시작 시에도 즉시 응답 가능.
5. **통합 라이브러리**: sub-tab이 아닌 단일 리스트로 사용자 정의 + 카탈로그 + 추천을 보여줌.
