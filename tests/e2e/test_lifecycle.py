#!/usr/bin/env python3
"""
핵심 라이프사이클 통합 테스트
실제 AWS 리소스를 사용하여 시나리오 실행 flow를 검증합니다.

테스트 항목:
1. webhook 전송 (incidentId 생성 + HMAC 서명 + 전송)
2. task 매칭 (get_backlog_task로 referenceId == incidentId 찾기)
3. journal records 조회 (task_id → 조사 메시지)
4. 장애 주입/해제 API
5. CW 알람 상태 확인
"""
import json
import os
import sys
import time

# Add parent to path
sys.path.insert(0, os.path.dirname(__file__))

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_PROFILE = os.environ.get("AWS_PROFILE", "member1-acc")
AGENT_SPACE_ID = os.environ.get("AGENT_SPACE_ID", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

# Set boto3 default session with profile
import boto3
boto3.setup_default_session(profile_name=AWS_PROFILE, region_name=AWS_REGION)

PASS = "✅"
FAIL = "❌"
SKIP = "⏭"
results = []


def test(name):
    def decorator(fn):
        def wrapper():
            try:
                ok, detail = fn()
                status = PASS if ok else FAIL
                results.append((status, name, detail))
                print(f"  {status} {name}: {detail}")
                return ok
            except Exception as e:
                results.append((FAIL, name, str(e)))
                print(f"  {FAIL} {name}: {e}")
                return False
        return wrapper
    return decorator


# ── Test 1: Webhook 전송 ──

@test("webhook 전송 (incidentId 생성 + HMAC)")
def test_webhook_send():
    from verifier import _send_webhook
    iid = _send_webhook("devops-agent-test-hasher-errors", "Test webhook from lifecycle test")
    if iid:
        return True, f"incidentId={iid}"
    return False, "webhook 전송 실패"


# ── Test 2: Task 매칭 (get_backlog_task) ──

@test("task 매칭 (referenceId == incidentId)")
def test_task_matching():
    from verifier import _send_webhook, _find_task_by_incident_id
    # 이전 테스트에서 보낸 webhook의 incidentId로 task 찾기
    # 실제로는 DevOps Agent가 task를 만들기까지 시간이 걸리므로 폴링
    iid = _send_webhook("devops-agent-test-hasher-errors", "Test matching")
    if not iid:
        return False, "webhook 전송 실패"
    
    # 최대 60초 폴링
    for i in range(12):
        task_id, status = _find_task_by_incident_id(iid)
        if task_id:
            return True, f"incidentId={iid[:30]} → task_id={task_id[:12]} status={status}"
        time.sleep(5)
    return False, f"60초 내 task 매칭 안 됨 (incidentId={iid[:30]})"


# ── Test 3: Journal Records 조회 ──

@test("journal records 조회 (task_id → 메시지)")
def test_journal_records():
    import boto3
    client = boto3.client("devops-agent", region_name=AWS_REGION)
    
    # 최근 완료된 task 찾기
    resp = client.list_backlog_tasks(agentSpaceId=AGENT_SPACE_ID, limit=10, order='DESC')
    completed = [t for t in resp.get('tasks', []) if t.get('status') == 'COMPLETED']
    if not completed:
        return False, "완료된 task 없음"
    
    task_id = completed[0]['taskId']
    exec_resp = client.list_executions(agentSpaceId=AGENT_SPACE_ID, taskId=task_id, limit=1)
    execs = exec_resp.get('executions', [])
    if not execs:
        return False, f"task={task_id[:12]}에 execution 없음"
    
    exec_id = execs[0]['executionId']
    jr = client.list_journal_records(agentSpaceId=AGENT_SPACE_ID, executionId=exec_id, limit=5, order='ASC')
    records = jr.get('records', [])
    return len(records) > 0, f"task={task_id[:12]} exec={exec_id[:12]} records={len(records)}"


# ── Test 4: 장애 주입/해제 API ──

@test("hasher 장애 주입/해제 (config/cache)")
def test_hasher_fault_injection():
    from verifier import _run_cmd
    # 주입
    ok1, out1, _ = _run_cmd(
        'kubectl exec -n dockercoins deployment/hasher -- python3 -c '
        '"import urllib.request; print(urllib.request.urlopen(\'http://localhost/config/cache?mode=aggressive&size=256\').read().decode())"',
        timeout=15
    )
    if not ok1 or 'aggressive' not in out1:
        return False, f"주입 실패: {out1}"
    
    # 해제
    ok2, out2, _ = _run_cmd(
        'kubectl exec -n dockercoins deployment/hasher -- python3 -c '
        '"import urllib.request; print(urllib.request.urlopen(\'http://localhost/config/cache?mode=normal\').read().decode())"',
        timeout=15
    )
    if not ok2 or 'normal' not in out2:
        return False, f"해제 실패: {out2}"
    
    return True, "주입(aggressive) → 해제(normal) 정상"


@test("rng 장애 주입/해제 (config/response)")
def test_rng_fault_injection():
    from verifier import _run_cmd
    ok1, out1, _ = _run_cmd(
        'kubectl exec -n dockercoins deployment/rng -- python3 -c '
        '"import urllib.request; print(urllib.request.urlopen(\'http://localhost/config/response?quality=degraded&rate=0.5\').read().decode())"',
        timeout=15
    )
    if not ok1 or 'degraded' not in out1:
        return False, f"주입 실패: {out1}"
    
    ok2, out2, _ = _run_cmd(
        'kubectl exec -n dockercoins deployment/rng -- python3 -c '
        '"import urllib.request; print(urllib.request.urlopen(\'http://localhost/config/response?quality=normal\').read().decode())"',
        timeout=15
    )
    if not ok2 or 'normal' not in out2:
        return False, f"해제 실패: {out2}"
    
    return True, "주입(degraded) → 해제(normal) 정상"


# ── Test 5: CW 알람 상태 확인 ──

@test("CW 알람 상태 조회")
def test_cw_alarm():
    import boto3
    cw = boto3.client("cloudwatch", region_name=AWS_REGION)
    resp = cw.describe_alarms(AlarmNamePrefix="devops-agent-test-")
    alarms = resp.get("MetricAlarms", [])
    if not alarms:
        return False, "알람 없음"
    states = {a['AlarmName'].replace('devops-agent-test-', ''): a['StateValue'] for a in alarms}
    return True, f"알람 {len(alarms)}개: {states}"


# ── Test 6: auto-restore (run.scenario 참조) ──

@test("auto-restore 변수 참조 (run.scenario)")
def test_auto_restore_variable():
    """run.scenario가 정상 참조되는지 확인 (이전 NameError 버그)"""
    from verifier import SimulationRun
    scenario = {"id": "test", "name": "test", "restore": {"command": "echo ok"}, "verification": {"steps": []}}
    run = SimulationRun(scenario)
    # to_dict에서 에러 안 나는지
    d = run.to_dict()
    # run.scenario 접근
    restore_cmd = run.scenario.get("restore", {}).get("command", "")
    return restore_cmd == "echo ok", f"restore_cmd='{restore_cmd}'"


# ── Run ──

if __name__ == "__main__":
    print("\n=== 핵심 라이프사이클 테스트 ===\n")
    
    # 빠른 단위 테스트 먼저
    test_auto_restore_variable()
    test_cw_alarm()
    test_hasher_fault_injection()
    test_rng_fault_injection()
    
    # 느린 통합 테스트 (핵심 — skip 금지)
    test_journal_records()
    test_webhook_send()
    test_task_matching()  # webhook → task 생성 → referenceId 매칭 검증
    
    print(f"\n=== 결과: {sum(1 for s,_,_ in results if s==PASS)}/{len(results)} pass ===")
    for s, n, d in results:
        if s == FAIL:
            print(f"  {s} {n}: {d}")
