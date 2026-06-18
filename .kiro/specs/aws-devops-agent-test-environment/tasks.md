# Implementation Plan: AWS DevOps Agent Test Environment

## Overview

AWS DevOps Agent의 유용성과 한계를 체계적으로 검증하기 위한 단계별 테스트 계획.
DockerCoins 앱 기반으로 "쉬운 것 → 어려운 것" 순서로 진행하여 Agent의 능력 경계를 찾는다.

## 완료된 작업

- [x] 1. 인프라 구성
- [x] 1.1 AWS 기본 인프라 (VPC, EKS, RDS)
- [x] 1.2 DockerCoins 테스트 앱 배포 (hasher, rng, webui, worker, redis)
- [x] 1.3 Agent Space 생성 및 AWS 계정 연동
- [x] 1.4 Application Signals (APM) 자동 계측 설정
  - rng (Python): ✅ Traces + Metrics (ADOT SDK)
  - worker (Python): ✅ Traces + Metrics (ADOT SDK)
  - webui (Node.js): ✅ Traces + Metrics (ADOT SDK)
  - hasher (Ruby): ✅ Traces only (수동 OpenTelemetry SDK)
    - Ruby용 ADOT SDK 없음 → Application Signals 메트릭 자동 생성 불가
    - Transaction Search로 트레이스 기반 분석 가능
  - redis: ⚠️ Remote only (계측 불가)
  - 해결한 이슈: 
    - 복수 Instrumentation CRD 충돌 → 단일 통합 CRD로 해결
    - Sinatra 4.x HostAuthorization 차단 → protection: false 설정
    - OTLP Exporter 명시적 설정 추가

## Tasks

- [-] 2. Phase 1: 기본 감지 능력 테스트 (Baseline)
  - 목표: Agent가 기본적인 문제를 감지하고 보고하는지 확인
  - 평가: 감지 시간, 알림 정확도, 기본 정보 제공

- [x] 2.1 단일 서비스 다운 감지 테스트
  - 방법: `kubectl scale deployment hasher -n dockercoins --replicas=0`
  - 검증: Agent가 hasher 서비스 다운을 감지하는지
  - 기록: 감지 시간, 알림 내용
  
  ### 테스트 결과 (2026-01-28)
  | 항목 | Agent 분석 | 실제 | 정확도 |
  |------|-----------|------|--------|
  | 증상 감지 | ✅ worker→hasher 연결 실패 1,089회 | ✅ 정확 | **정확** |
  | 근본 원인 | ❌ "networking issue after nodegroup migration" | hasher replicas=0 | **오진** |
  | 추가 분석 | CloudWatch operator, redis 문제 언급 | 관련 없음 | **노이즈** |
  
  **평가**: 
  - 증상 감지: ✅ 양호
  - 근본 원인 분석: ❌ 실패 (Deployment replicas 상태 미확인)
  - 유용성: **낮음** - 단순한 서비스 다운을 복잡한 네트워킹 문제로 오진

- [ ] 2.2 로그 기반 에러 감지 테스트
  - 방법: worker에서 강제 에러 로그 발생
  - 검증: Agent가 로그 에러를 감지하는지
  - 기록: 로그 분석 능력

- [ ] 2.3 CrashLoopBackOff 감지 테스트
  - 방법: 잘못된 명령어로 Pod 재배포
  - 검증: Agent가 CrashLoopBackOff 상태를 감지하는지
  - 기록: 상태 변화 감지 능력

- [ ] 3. Phase 2: 원인 분석 능력 테스트 (Root Cause Analysis)
  - 목표: Agent가 증상과 원인을 구분하는지 확인
  - 평가: 근본 원인 정확도, 의존성 체인 이해도

- [ ] 3.1 연쇄 장애 근본 원인 분석 (rng → worker)
  - 방법: rng Pod 삭제 → worker 에러 발생
  - 검증: Agent가 rng를 근본 원인으로 식별하는지 (worker가 아닌)
  - 기록: 의존성 분석 정확도

- [ ] 3.2 다중 영향 분석 (redis → worker + webui)
  - 방법: redis Pod 삭제 → worker, webui 동시 에러
  - 검증: Agent가 redis를 근본 원인으로 식별하는지
  - 기록: 다중 영향 분석 능력

- [ ] 3.3 설정 오류 원인 분석
  - 방법: worker의 REDIS_HOST를 잘못된 값으로 변경
  - 검증: Agent가 환경변수 설정 오류를 식별하는지
  - 기록: 설정 vs 코드 vs 인프라 구분 능력

- [ ] 3.4 리소스 문제 원인 분석
  - 방법: worker의 memory limit을 32Mi로 제한 → OOMKilled
  - 검증: Agent가 리소스 부족을 원인으로 식별하는지
  - 기록: 리소스 분석 능력

- [ ] 4. Phase 3: 해결 권고 능력 테스트 (Remediation)
  - 목표: Agent가 실행 가능한 해결책을 제시하는지 확인
  - 평가: 권고의 구체성, 실행 가능성, 안전성

- [ ] 4.1 Pod 복구 권고 테스트
  - 방법: hasher Pod 삭제
  - 검증: Agent가 구체적인 복구 명령어를 제시하는지
  - 기록: 권고 내용의 실행 가능성

- [ ] 4.2 이미지 태그 오류 권고 테스트
  - 방법: 존재하지 않는 이미지 태그로 배포
  - 검증: Agent가 올바른 태그를 제안하는지
  - 기록: ECR 이미지 분석 능력

- [ ] 4.3 리소스 조정 권고 테스트
  - 방법: 리소스 부족으로 OOMKilled 발생
  - 검증: Agent가 적정 리소스 값을 제안하는지
  - 기록: 리소스 권고의 합리성

- [ ] 4.4 스케일 권고 테스트
  - 방법: worker 부하 증가 시뮬레이션
  - 검증: Agent가 HPA 또는 replica 증가를 권고하는지
  - 기록: 스케일링 권고 능력

- [ ] 5. Phase 4: 복잡한 시나리오 테스트 (Complex Scenarios)
  - 목표: Agent의 한계 경계 찾기
  - 평가: 복잡한 상황에서의 정확도, 분석 깊이

- [ ] 5.1 간헐적 장애 감지 테스트
  - 방법: rng에 10% 확률로 500 에러 반환하도록 수정
  - 검증: Agent가 불안정한 상태를 감지하는지
  - 기록: 간헐적 문제 감지 능력

- [ ] 5.2 성능 저하 감지 테스트
  - 방법: hasher의 sleep 시간을 0.1s → 2s로 증가
  - 검증: Agent가 처리량 저하를 감지하는지
  - 기록: 메트릭 기반 분석 능력

- [ ] 5.3 동시 다중 장애 분석 테스트
  - 방법: rng + hasher 동시 다운
  - 검증: Agent가 복수 문제를 분리 분석하는지
  - 기록: 복합 장애 분석 능력

- [ ] 5.4 네트워크 정책 문제 테스트
  - 방법: NetworkPolicy로 worker → rng 통신 차단
  - 검증: Agent가 네트워크 정책 문제를 식별하는지
  - 기록: 네트워크 분석 능력

- [ ] 5.5 권한 문제 테스트
  - 방법: ECR 풀 권한 제거 → ImagePullBackOff
  - 검증: Agent가 IAM 권한 문제를 식별하는지
  - 기록: 보안/권한 분석 능력

- [ ] 6. Phase 5: 한계 테스트 (Edge Cases)
  - 목표: Agent가 실패하는 케이스 문서화
  - 평가: 한계 인정 여부, 실패 케이스 패턴

- [ ] 6.1 코드 버그 감지 테스트
  - 방법: worker 로직에 버그 주입 (잘못된 계산)
  - 예상: 감지 불가 (기능적 오류는 범위 밖)
  - 기록: 코드 레벨 분석 한계

- [ ] 6.2 데이터 정합성 문제 테스트
  - 방법: redis 데이터 손상 시뮬레이션
  - 예상: 감지 불가 (데이터 레벨은 범위 밖)
  - 기록: 데이터 분석 한계

- [ ] 6.3 외부 서비스 장애 테스트
  - 방법: 외부 API 호출 실패 시뮬레이션
  - 예상: 부분 감지 (로그 기반)
  - 기록: 외부 의존성 분석 한계

- [ ] 7. 결과 종합 및 문서화
- [ ] 7.1 테스트 결과 종합 분석
  - 유용성 높은 시나리오 정리
  - 한계가 명확한 시나리오 정리
  - Agent 활용 가이드라인 도출

- [ ] 7.2 DevOps Agent 평가 리포트 작성
  - 강점/약점 분석
  - 권장 사용 케이스
  - 비권장 사용 케이스
  - 개선 제안사항

## 테스트 결과 기록 템플릿

각 시나리오 완료 후 아래 형식으로 기록:

```markdown
## 시나리오: [이름]
- 난이도: ⭐⭐
- 주입 방법: [명령어]
- 예상 결과: [Agent가 해야 할 것]

### 실제 결과
- 감지: ✅/❌ (소요 시간: Xs)
- 원인 분석: ✅/❌ (정확도: %)
- 권고: ✅/❌ (실행 가능성: %)

### 평가
- 유용성: [높음/중간/낮음]
- 한계: [발견된 한계점]
```

## Notes

- 각 테스트 전 정상 상태(baseline) 확인 필수
- 테스트 후 반드시 수동 복구하여 다음 테스트에 영향 없도록
- Agent 반응 대기 시간: 최소 2분, 최대 10분
- 모든 결과는 `test-results/` 디렉토리에 기록
