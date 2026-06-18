---
description: 앱 시작 (overview_app.py:5003)
---

Dashboard 앱을 시작합니다.

1. 현재 실행 중인 프로세스 확인:
   ```bash
   lsof -i :5003
   ```
2. 포트 사용 중이면 사용자에게 알리고 중단
3. 앱 시작:
   ```bash
   cd services/dashboard && python overview_app.py
   ```
4. 시작 확인:
   ```bash
   curl -s http://localhost:5003/api/active-runs | python -m json.tool
   ```
