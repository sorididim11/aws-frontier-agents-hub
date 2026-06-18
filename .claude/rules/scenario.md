---
paths:
  - services/dashboard/routes_scenario.py
  - services/dashboard/verifier*.py
  - services/dashboard/engine*.py
  - services/dashboard/failure_modes.py
  - services/dashboard/scenarios/
---

# Scenario Engine 규칙

- Agent = read-only generator, App = executor (subprocess). Agent에게 실행 권한 부여 금지
- 커맨드는 반드시 클러스터에서 직접 테스트 후 JSON에 반영
- 규칙 발견 시 기존 모든 시나리오 JSON을 grep+fix (retroactive fix 원칙)
- 3 executor: Classic, Script, Engine(phased) — executor_type으로 분기
- Pipeline: preflight → cleanup → trigger → verify → restore 순서 엄수
- FIS pod-network-latency는 AppSignals에 보이지 않음 — app-level injection 사용
- OTEL init container는 pod 재시작 시에만 inject됨
