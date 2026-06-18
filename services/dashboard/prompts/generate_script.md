위 시나리오를 실행하는 **bash 스크립트**를 아래 표준 템플릿에 맞춰 만들어줘.

## 표준 스크립트 구조 (반드시 이 순서와 형식)
```
#!/bin/bash
set -e
export AWS_PROFILE="${{AWS_PROFILE:-{profile}}}"
export AWS_REGION="${{AWS_REGION:-{region}}}"
NAMESPACE="${{NAMESPACE:-dockercoins}}"

STEP=0
PASSED=0
TOTAL=<총 step 수>

checkpoint() {{
  STEP=$1
  local name="$2" status="$3" detail="$4"
  echo "CHECKPOINT|$STEP|$name|$status|$detail"
  if [ "$status" = "PASS" ]; then PASSED=$((PASSED+1)); fi
}}

# --- Step 1: 환경 사전 확인 ---
<대상 pod/service 존재 확인, alarm 현재 상태 확인>
checkpoint 1 "환경 사전 확인" "PASS|FAIL" "상세 내용"

# --- Step 2: Trigger (장애 주입) ---
<FIS 실험 시작 또는 kubectl 명령으로 장애 주입>
checkpoint 2 "장애 주입" "PASS|FAIL" "상세 내용"

# --- Step 3~N: Verification ---
<각 verification step마다 polling loop>
checkpoint N "step 이름" "PASS|FAIL" "상세 내용"

# --- 최종 결과 ---
echo "RESULT|$PASSED/$TOTAL"
if [ "$PASSED" -eq "$TOTAL" ]; then exit 0; else exit 1; fi
```

## 규칙
1. **언어는 반드시 bash** (python 스크립트 금지)
2. **checkpoint 함수**로 각 step 결과를 표준 형식(`CHECKPOINT|N|name|status|detail`)으로 출력
3. bash 3 호환: `declare -A` 금지, `set -euo pipefail` 대신 `set -e`
4. `wget` 금지 → `curl` 사용
5. JSON 파싱이 필요하면 `python3 -c "import json,sys; ..."` 인라인 사용
6. FIS 실험 실행: `aws fis start-experiment --experiment-template-id <ID>` 사용
7. 환경변수 `AWS_PROFILE`, `AWS_REGION`, `NAMESPACE`는 외부에서 주입됨 (기본값 포함)
8. alarm polling: `aws cloudwatch describe-alarms --alarm-names <name> --query 'MetricAlarms[0].StateValue' --output text`
9. kubectl 명령은 `kubectl -n $NAMESPACE` 형태로 namespace 지정
10. **중요: ApplicationSignals alarm + 지연 주입 시 반드시 application-level 방식 사용**
    - FIS `pod-network-latency`는 커널(tc netem) 레벨이라 ApplicationSignals Latency 메트릭에 반영 안됨
    - 서비스의 `/inject-latency?seconds=N` 엔드포인트를 `kubectl port-forward` + `curl`로 호출해야 함
    - 복원은 `/clear-latency` 엔드포인트 호출
    - 예시:
      ```
      kubectl -n $NAMESPACE port-forward svc/hasher 19090:80 &
      PF_PID=$!
      sleep 2
      curl -s "http://localhost:19090/inject-latency?seconds=5"
      # ... alarm polling ...
      curl -s "http://localhost:19090/clear-latency"
      kill $PF_PID 2>/dev/null
      ```

bash 코드 블록만 출력. 설명 불필요.