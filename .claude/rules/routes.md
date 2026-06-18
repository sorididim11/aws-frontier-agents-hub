---
paths:
  - services/dashboard/routes_*.py
---

# Routes 규칙

- kubectl context는 per-service로 cluster_manager가 해석 — 시나리오 단위가 아님
- Agent Space API 호출 시 `_profile_for_space(space_id)` 사용, 글로벌 AWS_PROFILE 금지
- 에러 응답은 명확한 메시지 + HTTP status code — silent fallback 시도 금지
- 거짓 fallback(존재하지 않는 endpoint, 더미 응답) 생성 금지 — 실제 app API endpoint를 확인하고 사용
- 인프라 정보는 IaC(CFn/CDK) 기반으로 확인 + app의 실제 API를 통해 조회 — 추측/하드코딩 금지
- 에러 발생 시 자동 retry 금지 — 즉시 실패시키고 명확한 에러 메시지 반환
- Long-running operation은 DDB event stream + SSE relay 패턴
- Blueprint 추가/제거 시 overview_app.py import 수정 필요 — 반드시 사용자 확인 후 진행
