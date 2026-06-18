---
paths:
  - services/dashboard/arch_*.py
  - services/dashboard/routes_arch.py
---

# Architecture Analysis 규칙

- 분석 실행 중 이 파일들을 절대 편집하지 말 것 — Flask auto-reload가 thread를 kill함
- 토폴로지 분석: single-app Q2 + boundary expansion이 기본. multi-app은 fallback only
- multi-app 강제 bypass 금지
- arch_worker.py는 subprocess로 실행됨 — DDB event stream으로 결과 전달
- AI 질문은 "출력 정의 + ONE question" 원칙 — 여러 질문 금지
- 분석 결과 출력은 한국어
