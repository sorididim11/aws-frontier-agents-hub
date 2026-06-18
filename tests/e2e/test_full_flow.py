#!/usr/bin/env python3
"""Full flow test: 시나리오 생성 → 스크립트 생성 → 실행.

DevOps Agent 채팅으로 시나리오 JSON + 실행 스크립트를 받고 실제 실행까지.
"""
import sys, os, time, json, re, subprocess, tempfile
sys.path.insert(0, os.path.dirname(__file__))

import boto3
from arch_analysis import AgentChatClient

SPACE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PROFILE = "member1-acc"

SCENARIO_FORMAT_EXAMPLE = """{
  "id": "string",
  "name": "string",
  "category": "string",
  "layer": "string",
  "purpose": "string (한국어)",
  "trigger": {"type": "kubectl|fis", "command": "string"},
  "pre_cleanup": {"command": "string", "reset_alarms": ["alarm-name"], "wait_ok_timeout": 120},
  "restore": {"command": "string"},
  "verification": {
    "steps": [
      {"name": "string", "type": "pod_logs|cw_alarm|metric_check|kubectl_check|pod_status", "timeout": 60, "poll_interval": 10, ...}
    ]
  }
}"""


def extract_code_blocks(text):
    """응답에서 코드 블록 추출."""
    blocks = []
    for m in re.finditer(r'```(\w*)\n(.*?)```', text, re.DOTALL):
        lang = m.group(1)
        code = m.group(2).strip()
        blocks.append({"lang": lang, "code": code})
    return blocks


def extract_json(text):
    """응답에서 JSON 추출."""
    blocks = extract_code_blocks(text)
    for b in blocks:
        if b["lang"] in ("json", ""):
            try:
                return json.loads(b["code"])
            except json.JSONDecodeError:
                continue
    # fallback: 텍스트에서 직접 JSON 추출
    for m in re.finditer(r'\{[\s\S]*\}', text):
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            continue
    return None


def extract_bash(text):
    """응답에서 bash 스크립트 추출."""
    blocks = extract_code_blocks(text)
    for b in blocks:
        if b["lang"] in ("bash", "sh", "shell"):
            return b["code"]
    return None


def run_script(script, timeout=120):
    """bash 스크립트를 실행하고 결과 반환."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
        f.write("#!/bin/bash\nset -e\n" + script)
        f.flush()
        script_path = f.name

    os.chmod(script_path, 0o755)
    print(f"\n{'='*60}")
    print(f"EXECUTING SCRIPT: {script_path}")
    print(f"{'='*60}\n")

    # macOS bash 3 호환성: declare -A 제거, set -euo → set -e
    with open(script_path, 'r') as f:
        fixed = f.read()
    fixed = fixed.replace('declare -A STEP_RESULTS\n', '')
    fixed = fixed.replace('STEP_RESULTS["$step_name"]=$result\n', '')
    fixed = fixed.replace('set -euo pipefail', 'set -e')
    # ${STEP_RESULTS[@]} 참조 제거
    fixed = re.sub(r'for step_name in "\$\{!STEP_RESULTS\[@\]\}".*?done', '', fixed, flags=re.DOTALL)
    with open(script_path, 'w') as f:
        f.write(fixed)

    try:
        result = subprocess.run(
            ["bash", script_path],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "AWS_PROFILE": PROFILE, "AWS_REGION": "us-east-1"}
        )
        print("STDOUT:")
        print(result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout)
        if result.stderr:
            print("\nSTDERR:")
            print(result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr)
        print(f"\nExit code: {result.returncode}")
        return result
    except subprocess.TimeoutExpired:
        print(f"TIMEOUT after {timeout}s")
        return None
    finally:
        os.unlink(script_path)


def main():
    session = boto3.Session(profile_name=PROFILE, region_name="us-east-1")
    client = AgentChatClient(SPACE_ID, session)
    exec_id = client.create_session()
    print(f"Session: {exec_id}\n")

    # ── Turn 1: 시나리오 생성 요청 ──
    q1 = f"""dockercoins 앱에서 hasher pod를 삭제하여 서비스 중단을 일으키는 장애 시나리오를 만들어줘.

시나리오 JSON 포맷:
{SCENARIO_FORMAT_EXAMPLE}

조건:
- hasher pod 삭제 후 자동 복구되는 시나리오
- verification에서 pod가 재시작되는지, CW alarm이 트리거되는지 확인
- 실제 존재하는 alarm 이름과 pod 이름 사용
- 한국어로 작성
- JSON만 출력"""

    print(f"{'='*60}")
    print("TURN 1: 시나리오 생성 요청")
    print(f"{'='*60}")
    t0 = time.time()
    resp1 = client.ask(exec_id, q1)
    print(f"Time: {time.time()-t0:.1f}s")
    text1 = resp1.final_text or resp1.raw_text or ""
    print(f"Response ({len(text1)} chars):")
    print(text1[:4000])

    scenario = extract_json(text1)
    if scenario:
        print(f"\n✅ JSON 파싱 성공: {json.dumps(scenario, indent=2, ensure_ascii=False)[:1000]}")
    else:
        print("\n❌ JSON 파싱 실패")
        print("Raw text로 계속 진행...")

    # ── Turn 2: 실행 스크립트 생성 요청 ──
    q2 = """위 시나리오를 실행하는 bash 스크립트를 만들어줘.

스크립트 요구사항:
1. 환경 사전 확인 (hasher pod 존재, alarm 현재 상태)
2. pre_cleanup 실행 (alarm 리셋)
3. trigger 실행 (pod 삭제)
4. verification step을 순서대로 실행 - 각 step마다 timeout과 poll_interval로 반복 체크
5. 각 step 결과를 PASS/FAIL로 출력
6. 최종 결과 요약

bash 코드 블록만 출력. 설명 불필요."""

    print(f"\n\n{'='*60}")
    print("TURN 2: 실행 스크립트 요청")
    print(f"{'='*60}")
    t0 = time.time()
    resp2 = client.ask(exec_id, q2)
    print(f"Time: {time.time()-t0:.1f}s")
    text2 = resp2.final_text or resp2.raw_text or ""
    print(f"Response ({len(text2)} chars):")
    print(text2[:5000])

    script = extract_bash(text2)
    if script:
        print(f"\n✅ Bash 스크립트 추출 성공 ({len(script)} chars)")
        print(f"Script preview:\n{script[:500]}...")
    else:
        print("\n❌ Bash 스크립트 추출 실패")
        return

    # ── Turn 3: 실제 실행 ──
    print(f"\n\n{'='*60}")
    print("TURN 3: 스크립트 실행")
    print(f"{'='*60}")
    result = run_script(script, timeout=180)

    # ── 결과 요약 ──
    print(f"\n\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"시나리오 생성: {'✅' if scenario else '❌'}")
    print(f"스크립트 생성: {'✅' if script else '❌'}")
    print(f"스크립트 실행: {'✅' if result and result.returncode == 0 else '❌'}")


if __name__ == "__main__":
    main()
