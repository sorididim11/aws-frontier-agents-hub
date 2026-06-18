"""
CFn Import 통합 테스트 — 테스트 전용 Space 생성 → Import → 검증 → Disconnect → 정리.

Usage:
    # Full cycle: 테스트 Space 생성 → Import → Disconnect → 삭제
    python tests/integration/test_cfn_import.py

    # Import만 (Space + Stack 유지)
    python tests/integration/test_cfn_import.py --import-only

    # 기존 Space로 테스트 (새로 생성 안 함)
    python tests/integration/test_cfn_import.py --space-id <id>

    # Disconnect만 (이미 import된 Space)
    python tests/integration/test_cfn_import.py --disconnect-only --space-id <id>

환경:
    - Dashboard가 localhost:5003에서 실행 중이어야 함
    - AWS profile member2-acc (222222222222) 접근 가능해야 함
"""
import argparse
import json
import sys
import time
import traceback

import boto3
import requests

BASE = "http://localhost:5003"
TIMEOUT = 300
AWS_PROFILE = "member2-acc"
AWS_REGION = "us-east-1"
TEST_SPACE_NAME = "cfn-import-test"
ACCOUNT_ID = "222222222222"
ROLE_ARN = "arn:aws:iam::222222222222:role/my-agent-space-devops-agent-role"


class Colors:
    OK = "\033[92m"
    FAIL = "\033[91m"
    WARN = "\033[93m"
    INFO = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def log(level, msg):
    c = {"OK": Colors.OK, "FAIL": Colors.FAIL, "WARN": Colors.WARN, "INFO": Colors.INFO}
    print(f"{c.get(level, '')}{level}{Colors.RESET} {msg}")


def api_get(path):
    r = requests.get(f"{BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def api_post(path, data=None):
    r = requests.post(f"{BASE}{path}", json=data or {}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ===================================================================
# AWS Direct — 테스트 Space 생성/정리
# ===================================================================


def aws_client():
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    return session.client("devops-agent")


def aws_cfn_client():
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    return session.client("cloudformation")


def create_test_space():
    """테스트 전용 Agent Space + AWS association 생성."""
    client = aws_client()

    # 기존 테스트 space 확인
    resp = client.list_agent_spaces()
    spaces = resp.get("agentSpaces", resp.get("agentSpaceSummaries", []))
    for sp in spaces:
        if sp.get("name", "").startswith(TEST_SPACE_NAME):
            sid = sp["agentSpaceId"]
            log("WARN", f"기존 테스트 Space 발견: {sid} — 재사용")
            return sid

    # 새 Space 생성
    log("INFO", f"테스트 Space 생성: {TEST_SPACE_NAME}")
    resp = client.create_agent_space(name=f"{TEST_SPACE_NAME}-agent-space")
    space_id = resp.get("agentSpaceId", resp.get("agentSpace", {}).get("agentSpaceId", ""))
    if not space_id:
        log("FAIL", f"Space 생성 실패: {json.dumps(resp)[:200]}")
        return None

    log("OK", f"Space 생성됨: {space_id}")

    # Space 준비 대기
    for _ in range(20):
        try:
            sp = client.get_agent_space(agentSpaceId=space_id)
            status = sp.get("agentSpace", sp).get("status", "")
            if status == "ACTIVE":
                break
        except Exception:
            pass
        time.sleep(3)

    # 여러 Association 추가 (AWS monitor + sourceAws cross-account)
    SECONDARY_ACCOUNT = "111111111111"
    SECONDARY_ROLE = "arn:aws:iam::111111111111:role/devops-agent-test-m1-CrossAccountDevOpsAgentRole"
    associations_to_add = [
        ("aws", {"aws": {"assumableRoleArn": ROLE_ARN, "accountId": ACCOUNT_ID, "accountType": "monitor"}}),
        ("aws", {"sourceAws": {"assumableRoleArn": SECONDARY_ROLE, "accountId": SECONDARY_ACCOUNT, "accountType": "source"}}),
    ]
    for svc_id, config in associations_to_add:
        log("INFO", f"Association 추가: {svc_id}...")
        try:
            assoc_resp = client.associate_service(
                agentSpaceId=space_id,
                serviceId=svc_id,
                configuration=config,
            )
            log("OK", f"  {svc_id} 추가됨: {assoc_resp.get('associationId', '?')[:20]}")
        except Exception as e:
            log("WARN", f"  {svc_id} 추가 실패 (무시): {e}")
    time.sleep(2)

    # 태그 추가
    try:
        sts = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION).client("sts")
        acct = sts.get_caller_identity()["Account"]
        space_arn = f"arn:aws:aidevops:{AWS_REGION}:{acct}:agentspace/{space_id}"
        client.tag_resource(resourceArn=space_arn, tags={"App": TEST_SPACE_NAME})
        log("OK", "App 태그 추가됨")
    except Exception as e:
        log("WARN", f"태그 추가 실패 (무시): {e}")

    return space_id


def register_test_space(space_id):
    """Dashboard에 테스트 Space 등록 (discover/register)."""
    log("INFO", "Dashboard에 Space 등록...")
    d = api_post("/api/spaces/discover/register", {
        "space_id": space_id,
        "space_type": "devops",
        "account_id": ACCOUNT_ID,
        "profile": AWS_PROFILE,
        "app_name": TEST_SPACE_NAME,
        "app_tag_value": TEST_SPACE_NAME,
        "cfn_import": False,  # 등록만, import는 별도 step에서
    })
    if d.get("ok"):
        log("OK", "Dashboard 등록 완료")
        return True
    else:
        log("FAIL", f"등록 실패: {d.get('error')}")
        return False


def cleanup_test_space(space_id):
    """테스트 Space 삭제 (CFn 스택 + Association + Space)."""
    log("INFO", f"테스트 Space 정리: {space_id}")

    # 1. CFn 스택 삭제 (DeletionPolicy:Retain이 적용돼 있으므로 리소스 보존됨)
    cfn = aws_cfn_client()
    stack_name = f"{TEST_SPACE_NAME}-agent-space-devops-agent"
    try:
        resp = cfn.describe_stacks(StackName=stack_name)
        stacks = resp.get("Stacks", [])
        if stacks and stacks[0].get("StackStatus") != "DELETE_COMPLETE":
            log("INFO", f"  스택 삭제: {stack_name}")
            cfn.delete_stack(StackName=stack_name)
            for _ in range(30):
                try:
                    r = cfn.describe_stacks(StackName=stack_name)
                    if not r.get("Stacks") or r["Stacks"][0].get("StackStatus") == "DELETE_COMPLETE":
                        break
                    time.sleep(5)
                except Exception:
                    break
            log("OK", "  스택 삭제됨")
    except Exception:
        pass  # 스택 없음

    # 2. Association 해제
    client = aws_client()
    try:
        assocs = client.list_associations(agentSpaceId=space_id).get("associations", [])
        for a in assocs:
            aid = a.get("associationId", "")
            if aid:
                try:
                    client.disassociate_service(agentSpaceId=space_id, associationId=aid)
                    log("INFO", f"  Association 해제: {aid[:20]}...")
                except Exception:
                    pass
    except Exception:
        pass

    # 3. Space 삭제
    try:
        client.delete_agent_space(agentSpaceId=space_id)
        log("OK", "  Space 삭제됨")
    except Exception as e:
        log("WARN", f"  Space 삭제 실패: {e}")

    # 4. DDB 레코드 삭제
    try:
        session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        tbl = session.resource("dynamodb").Table("devops-agent-test-m2-scenario-runs")
        tbl.delete_item(Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"})
        log("OK", "  DDB 레코드 삭제됨")
    except Exception:
        pass


# ===================================================================
# Test Steps
# ===================================================================


def step_health_check():
    """Dashboard 접근 확인."""
    try:
        d = api_get("/api/spaces/registry")
        count = len(d.get("spaces", []))
        log("OK", f"Dashboard 접속 성공 (등록된 Space: {count}개)")
        return True
    except Exception as e:
        log("FAIL", f"Dashboard 접속 불가: {e}")
        return False


def step_setup_space(space_id=None):
    """테스트 Space 준비 (생성 또는 기존 사용)."""
    if space_id:
        log("INFO", f"기존 Space 사용: {space_id}")
        return space_id

    sid = create_test_space()
    if not sid:
        return None

    # Dashboard에 등록 여부 확인 (settings에서 name이 있으면 등록됨)
    try:
        settings = api_get(f"/api/spaces/{sid}/settings")
        if settings.get("ok") and settings.get("name"):
            log("INFO", f"이미 Dashboard에 등록됨: {settings.get('name')}")
            return sid
    except Exception:
        pass

    # 미등록 → 등록
    if register_test_space(sid):
        return sid
    return sid


def step_verify_associations(space_id):
    """Space에 association이 존재하는지 확인."""
    client = aws_client()
    assocs = client.list_associations(agentSpaceId=space_id).get("associations", [])
    log("INFO", f"Association 수: {len(assocs)}")
    for a in assocs:
        aid = a.get("associationId", "")
        sid_val = a.get("serviceId", "")
        log("INFO", f"  - {sid_val} ({aid[:20]}...)")
    if not assocs:
        log("FAIL", "Association이 없음 — Import할 대상이 없습니다")
        return None
    return len(assocs)


def step_cfn_import(space_id):
    """CFn Import 실행."""
    log("INFO", "CFn Import 시작...")
    start = time.time()

    d = api_post(f"/api/spaces/{space_id}/cfn-import")
    elapsed = time.time() - start

    if d.get("ok"):
        log("OK", f"CFn Import 성공 ({elapsed:.1f}초)")
        log("OK", f"  Stack: {d.get('stack_name')}")
        log("OK", f"  Import된 리소스: {d.get('resources_imported')}개")
        log("OK", f"  최종 상태: {d.get('status')}")
        return d
    else:
        log("FAIL", f"CFn Import 실패 ({elapsed:.1f}초): {d.get('error')}")
        return None


def step_verify_import(space_id, expected_assoc_count):
    """Import 후 검증."""
    errors = []

    # 1. cfn-status
    status = api_get(f"/api/spaces/{space_id}/cfn-status")
    if status.get("deploy_method") != "cloudformation":
        errors.append(f"deploy_method={status.get('deploy_method')}")
    if not status.get("stack_name"):
        errors.append("stack_name 비어있음")
    if not status.get("can_disconnect"):
        errors.append("can_disconnect=false")
    if status.get("can_import"):
        errors.append("can_import=true (false여야 함)")

    # 2. Stack에서 리소스 확인
    cfn = aws_cfn_client()
    stack_name = status.get("stack_name", "")
    if stack_name:
        try:
            resources = cfn.list_stack_resources(StackName=stack_name).get("StackResourceSummaries", [])
            agent_space_res = [r for r in resources if r["ResourceType"] == "AWS::DevOpsAgent::AgentSpace"]
            assoc_res = [r for r in resources if r["ResourceType"] == "AWS::DevOpsAgent::Association"]
            log("INFO", f"  Stack 리소스: AgentSpace={len(agent_space_res)}, Association={len(assoc_res)}")

            if len(agent_space_res) != 1:
                errors.append(f"AgentSpace 리소스 수={len(agent_space_res)} (1이어야 함)")
            if len(assoc_res) != expected_assoc_count:
                errors.append(f"Association 리소스 수={len(assoc_res)} (expected={expected_assoc_count})")

            # DeletionPolicy 확인 (template에서)
            template = cfn.get_template(StackName=stack_name).get("TemplateBody", {})
            if isinstance(template, str):
                import yaml
                template = yaml.safe_load(template)
            for rname, rdef in template.get("Resources", {}).items():
                if rdef.get("DeletionPolicy") != "Retain":
                    errors.append(f"리소스 {rname}에 DeletionPolicy:Retain 없음")
                    break
        except Exception as e:
            errors.append(f"Stack 리소스 확인 실패: {e}")

    # 3. AWS 실제 association 여전히 존재
    client = aws_client()
    assocs = client.list_associations(agentSpaceId=space_id).get("associations", [])
    if len(assocs) != expected_assoc_count:
        errors.append(f"AWS association 수 변경: {len(assocs)} (expected={expected_assoc_count})")

    if errors:
        for e in errors:
            log("FAIL", f"  {e}")
        return False

    log("OK", "Import 검증 통과:")
    log("OK", f"  deploy_method=cloudformation, stack={stack_name}")
    log("OK", f"  Stack 리소스: 1 AgentSpace + {expected_assoc_count} Associations (모두 Retain)")
    log("OK", f"  AWS association 보존: {len(assocs)}개")
    return True


def step_cfn_disconnect(space_id):
    """CFn Disconnect 실행."""
    log("INFO", "CFn Disconnect 시작...")
    start = time.time()

    d = api_post(f"/api/spaces/{space_id}/cfn-disconnect")
    elapsed = time.time() - start

    if d.get("ok"):
        log("OK", f"CFn Disconnect 성공 ({elapsed:.1f}초)")
        return d
    else:
        log("FAIL", f"CFn Disconnect 실패 ({elapsed:.1f}초): {d.get('error')}")
        return None


def step_verify_disconnect(space_id, expected_assoc_count):
    """Disconnect 후 검증 — 앱에서 분리됨, AWS 스택/리소스는 그대로."""
    errors = []

    # 1. cfn-status: 앱에서 더 이상 관리 안 함
    status = api_get(f"/api/spaces/{space_id}/cfn-status")
    if status.get("deploy_method") == "cloudformation":
        errors.append("deploy_method 여전히 cloudformation")
    if not status.get("can_import"):
        errors.append("can_import=false (재 import 가능해야 함)")

    # 2. Stack은 AWS에 그대로 존재해야 함 (삭제 아님)
    cfn = aws_cfn_client()
    stack_name = f"{TEST_SPACE_NAME}-agent-space-devops-agent"
    stack_exists = False
    try:
        resp = cfn.describe_stacks(StackName=stack_name)
        stacks = resp.get("Stacks", [])
        if stacks and stacks[0].get("StackStatus") != "DELETE_COMPLETE":
            stack_exists = True
            log("INFO", f"  Stack 유지 확인: {stack_name} ({stacks[0]['StackStatus']})")
    except Exception:
        pass
    if not stack_exists:
        errors.append(f"스택 {stack_name}이 삭제됨 (유지되어야 함)")

    # 3. AWS association 보존 확인
    client = aws_client()
    assocs = client.list_associations(agentSpaceId=space_id).get("associations", [])
    if len(assocs) != expected_assoc_count:
        errors.append(f"Association 수 변경: {len(assocs)} (expected={expected_assoc_count})")

    if errors:
        for e in errors:
            log("FAIL", f"  {e}")
        return False

    log("OK", "Disconnect 검증 통과:")
    log("OK", f"  deploy_method='' (앱에서 분리됨)")
    log("OK", f"  AWS 스택 유지: {stack_name}")
    log("OK", f"  AWS association 보존: {len(assocs)}개")
    return True


# ===================================================================
# Main
# ===================================================================


def run_test(space_id=None, import_only=False, disconnect_only=False, no_cleanup=False):
    """Full test cycle."""
    print(f"\n{'='*60}")
    print(f"{Colors.BOLD} CFn Import 통합 테스트{Colors.RESET}")
    print(f"{'='*60}\n")

    results = {"passed": 0, "failed": 0}
    created_space = None

    def check(name, fn, *args):
        print(f"\n--- {name} ---")
        try:
            result = fn(*args)
            if result is None or result is False:
                results["failed"] += 1
            else:
                results["passed"] += 1
            return result
        except Exception as e:
            log("FAIL", f"예외 발생: {e}")
            traceback.print_exc()
            results["failed"] += 1
            return None

    # 1. Health
    if not check("1. Dashboard Health Check", step_health_check):
        return 1

    # 2. Space 준비
    sid = check("2. 테스트 Space 준비", step_setup_space, space_id)
    if not sid:
        return 1
    if not space_id:
        created_space = sid

    # 3. Association 확인
    assoc_count = check("3. Association 확인", step_verify_associations, sid)
    if not assoc_count:
        if created_space:
            cleanup_test_space(created_space)
        return 1

    try:
        if disconnect_only:
            if check("4. CFn Disconnect", step_cfn_disconnect, sid):
                check("5. Disconnect 검증", step_verify_disconnect, sid, assoc_count)
        else:
            # Import
            import_result = check("4. CFn Import", step_cfn_import, sid)
            if import_result:
                check("5. Import 검증", step_verify_import, sid, assoc_count)

                if not import_only:
                    time.sleep(3)
                    if check("6. CFn Disconnect", step_cfn_disconnect, sid):
                        check("7. Disconnect 검증", step_verify_disconnect, sid, assoc_count)
    finally:
        # Cleanup
        if created_space and not no_cleanup and not import_only:
            print(f"\n--- Cleanup ---")
            cleanup_test_space(created_space)

    # Summary
    print(f"\n{'='*60}")
    total = results["passed"] + results["failed"]
    color = Colors.OK if results["failed"] == 0 else Colors.FAIL
    print(f"{color}{Colors.BOLD}결과: {results['passed']}/{total} 통과{Colors.RESET}")
    if results["failed"]:
        print(f"{Colors.FAIL}  실패: {results['failed']}건{Colors.RESET}")
    if created_space and import_only:
        print(f"\n{Colors.WARN}NOTE: --import-only 모드. Space/Stack이 유지됩니다.{Colors.RESET}")
        print(f"  Space ID: {created_space}")
        print(f"  정리: python {__file__} --disconnect-only --space-id {created_space}")
    print(f"{'='*60}\n")

    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CFn Import 통합 테스트")
    parser.add_argument("--space-id", help="기존 Space ID로 테스트 (새로 생성 안 함)")
    parser.add_argument("--import-only", action="store_true", help="Import만 (Disconnect/정리 생략)")
    parser.add_argument("--disconnect-only", action="store_true", help="Disconnect만")
    parser.add_argument("--no-cleanup", action="store_true", help="테스트 후 Space 삭제 안 함")
    parser.add_argument("--base-url", default=BASE, help="Dashboard URL")
    args = parser.parse_args()

    BASE = args.base_url
    sys.exit(run_test(args.space_id, args.import_only, args.disconnect_only, args.no_cleanup))
