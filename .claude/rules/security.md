---
paths:
  - services/dashboard/routes_security_*.py
  - services/dashboard/security_*.py
---

# Security 모듈 규칙

- DevOps Space ↔ Security Space (1:N) 관계
- 캐시 3계층: Memory(5min) → Disk(.skill-cache/) → DDB — TTL별 계층화
- pentest → findings → chain attack → fix priority → remediation(PR) 파이프라인
- Agent Space API 호출 시 항상 `_profile_for_space(space_id)` 사용
