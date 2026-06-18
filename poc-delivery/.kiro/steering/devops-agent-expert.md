---
inclusion: always
---

# DevOps Agent 전문가 — 프로젝트 규칙

## 프로젝트 규칙

- 모든 응답은 **한국어**
- AWS CLI 명령에 반드시 `--profile {PROFILE} --region {REGION} --no-cli-pager` 포함
- CFn 리소스 이름에 프로젝트명 접두사 사용 (충돌 방지)
- 고객 입력값은 `{플레이스홀더}` 형태로 표시하고, 실행 전 반드시 확인
- 파괴적 작업(delete, terminate) 전에 반드시 확인 요청
- 답변/코드를 검증 없이 고객에게 전달하지 않는다
