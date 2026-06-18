---
paths:
  - services/dashboard/skill_manager.py
  - services/dashboard/catalog_manager.py
  - services/dashboard/routes_skills.py
  - skills/
---

# Skill 모듈 규칙

- SKILL.md 직접 편집 금지 — `src/*.md` + `build.sh`로 자동 조립
- 모든 CRUD는 Asset API 단일 경로 (채팅 fallback 없음)
- Asset API 응답: key='items', assetType 소문자만, space_id는 UUID
- ZIP deploy: skills/ 디렉토리 편집 → deploy() → ZIP → Asset API
- DEFAULT_SKILLS: arch-discover, k8s-detail (아키텍처 분석 시 자동 배포)
