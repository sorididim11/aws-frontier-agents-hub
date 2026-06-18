---
description: 모듈 API 탐색 및 문서 확인
---

특정 모듈의 API와 관련 문서를 빠르게 확인합니다.

$ARGUMENTS 에 모듈명을 받습니다 (예: arch_analysis, skill_manager)

1. 모듈 함수 목록:
   ```bash
   grep -n "def " services/dashboard/$ARGUMENTS.py | head -30
   ```
2. 관련 문서 확인:
   ```bash
   grep -l "$ARGUMENTS" docs/*.md
   ```
3. 최근 변경:
   ```bash
   git log --oneline -5 -- services/dashboard/$ARGUMENTS.py
   ```
4. 결과를 요약해서 보고
