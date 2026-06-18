# 구현 계획: Hasher X-Ray Fault Trace 버그 수정

## 조사 결과

- [x] 1. 근본 원인 분석
  - [x] 1.1 X-Ray에 나타나는 span vs 안 나타나는 span 비교
  - [x] 1.2 대량 호출 테스트 (GET / 120회, GET /error 130회)
  - [x] 1.3 /error fault trace X-Ray 표시 확인 → **정상 표시됨 (fault=true)**
  - [x] 1.4 근본 원인 확정: CW Agent 내부 sampling (~2-5%)

## 결론

hasher 코드 변경 불필요. /error, /slow 엔드포인트의 계측은 정상 동작.
문제는 CW Agent Application Signals pipeline의 내부 sampling이 낮은 비율로 적용되어,
소수 호출 시 X-Ray에 나타나지 않았던 것.

## 남은 작업

- [ ] 2. CW Agent sampling 비율 개선 (선택)
  - [ ] 2.1 CW Agent addon config에서 sampling 관련 설정 조사
  - [ ] 2.2 또는 별도 OTel Collector 배포로 CW Agent 우회

- [ ] 3. 테스트 시나리오 조정
  - [ ] 3.1 test-scenarios.yaml에서 /error, /slow 호출 횟수를 100회+로 증가
  - [ ] 3.2 DevOps Agent 테스트 시 충분한 데이터 확보

- [x] 4. 정리
  - [x] 4.1 tasks/lessons.md 업데이트
  - [x] 4.2 design.md 최종 결과 기록
