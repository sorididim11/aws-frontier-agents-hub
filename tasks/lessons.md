# Lessons Learned

## 작업 완료 후 시뮬레이터 항상 실행
- 대시보드 관련 작업이 끝나면 반드시 `kubectl port-forward svc/dashboard 8081:80 -n dashboard`를 백그라운드로 실행해서 사용자가 바로 테스트할 수 있게 할 것
- 물어보지 말고 그냥 열어둘 것

## CloudWatch 알람 상태 전환 문제
- CW 알람은 상태 전환(OK→ALARM)시에만 Lambda를 호출함. 이미 ALARM 상태면 재호출 안 됨
- 시나리오 trigger에 사전 복원 + 알람 강제 OK 리셋을 포함시켜야 매번 클린 실행 가능
- `aws cloudwatch set-alarm-state`로 강제 리셋 가능

## 시나리오 복원 미실행 문제
- 테스트 후 restore를 안 하면 다음 실행에 영향을 줌
- trigger 자체에 pre-cleanup을 넣는 것이 가장 안전한 패턴

## Docker 빌드 타임아웃
- `docker build -q` 옵션은 타임아웃에 걸릴 수 있음
- 빌드는 `controlBashProcess`로 백그라운드 실행 후 `getProcessOutput`으로 확인

## AWS CLI 필수 옵션
- 항상 `--profile member1-acc --region us-east-1 --no-cli-pager` 사용
- 파이프 명령 시 `export AWS_PAGER=""` 선행
- Pod 내부(IRSA)에서는 `--profile` 제거

## Python heredoc 금지
- bash에서 Python heredoc 사용 시 Signal Cancelled 발생
- Python 스크립트는 파일로 작성 후 실행
