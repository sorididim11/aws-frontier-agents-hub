# Dashboard Simulator 3대 버그 수정 계획

## 문제 정의

### 🔴 P0: Gunicorn Multi-Worker 상태 불일치 (현재 문제)
- **근본 원인**: gunicorn `--workers 2`로 실행 → `_active_runs` 딕셔너리가 프로세스 메모리에만 존재 → run을 시작한 worker와 status를 폴링하는 worker가 다르면 "Run not found"
- **증상**: UI에서 "트리거 실행중"에서 멈춤, 실행 결과 저장 안 됨
- **수정**: Dockerfile CMD를 `--workers 1 --threads 8`로 변경

### 🟡 P1: Slack 조사 메시지 스레드 답변 미표시
- **근본 원인**: `conversations.history` API는 top-level 메시지만 반환. DevOps Agent의 실제 조사 내용(Finding, Observation 등)은 모두 스레드 답변(thread replies)으로 올라감 → `conversations.replies` API를 호출해야 함
- **증상**: "Investigation started..." 한 줄만 보이고 실제 조사 결과는 안 보임
- **수정**: verifier.py의 `get_slack_messages()`에서 각 메시지의 `reply_count > 0`이면 `conversations.replies`로 스레드 내용도 가져오기. UI에서 스레드 답변을 들여쓰기로 표시

### 🟡 P2: 실행 이력 영구 저장 안 됨
- **근본 원인**: `/app/results/`가 컨테이너 ephemeral storage → Pod 재시작/재배포 시 전부 소실. PVC 없음
- **증상**: 배포할 때마다 이력 초기화
- **수정**: PVC 생성 후 `/app/results`에 마운트

---

## 실행 계획

- [x] 1. P0: Gunicorn single-worker 수정
  - [x] 1.1 Dockerfile CMD를 `--workers 1 --threads 8` + `PYTHONUNBUFFERED=1` 환경변수 추가
  - [x] 1.2 deployment yaml에 command/args/env 반영 (source of truth)
  - [x] 1.3 Pod 정상 동작 확인 (single worker, Slack config loaded)
- [x] 2. P1: Slack 스레드 답변 조회
  - [x] 2.1 verifier.py `get_slack_messages()`에 `conversations.replies` API로 thread replies 조회 로직 추가
  - [x] 2.2 index.html에서 스레드 답변을 `thread-reply` CSS 클래스로 들여쓰기 표시 + `↳` prefix
  - [x] 2.3 kubectl cp → gunicorn reload → API 테스트로 thread replies 포함 확인
- [x] 3. P2: 실행 이력 PVC 영구 저장
  - [x] 3.1 dashboard.yaml에 PVC (EBS gp2, 1Gi) 추가
  - [x] 3.2 deployment에 volumeMount `/app/results` 추가
  - [x] 3.3 EBS CSI driver addon 설치 + IAM 권한(EBSCSIVolumeAccess 인라인 정책) 추가
  - [x] 3.4 PVC Bound, Pod Running, `/app/results` 마운트 확인 (974M available)
- [x] 4. 최종 검증
  - [x] 4.1 port-forward 열어서 사용자 테스트 가능하게
