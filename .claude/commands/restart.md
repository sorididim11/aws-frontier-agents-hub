---
description: 안전한 앱 재시작 (active-runs 확인 후)
---

Dashboard 앱을 안전하게 재시작합니다.

1. 진행 중 작업 확인:
   ```bash
   curl -s http://localhost:5003/api/active-runs | python -m json.tool
   ```
2. 진행 중인 작업이 있으면:
   - 사용자에게 알리고 확인 요청
   - in-memory 작업은 재시작 시 소실됨을 경고
3. 확인 후 재시작:
   ```bash
   lsof -ti :5003 | xargs kill -9 2>/dev/null
   cd services/dashboard && python overview_app.py
   ```
4. 재시작 확인:
   ```bash
   sleep 2 && curl -s http://localhost:5003/api/active-runs
   ```
