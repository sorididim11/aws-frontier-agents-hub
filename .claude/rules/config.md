---
paths:
  - services/dashboard/config.yaml
  - services/dashboard/app_config.py
---

# Config 규칙

- config.yaml 구조 변경 시 반드시 사용자 확인
- app_config.py의 `_CFG`, `reload_cfg`가 전체 앱 설정의 진입점
- 변수 해석: `${PROJECT_NAME}`, `${NAMESPACE}` + discovery
- 앱 진입점: overview_app.py:5003 (NOT app.py:8080)
