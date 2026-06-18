#!/usr/bin/env python3
"""DevOps Agent 채팅에서 코드 생성 요청 테스트."""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))

import boto3
from arch_analysis import AgentChatClient

SPACE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PROFILE = "member1-acc"


def test_codegen():
    session = boto3.Session(profile_name=PROFILE, region_name="us-east-1")
    client = AgentChatClient(SPACE_ID, session)
    exec_id = client.create_session()
    print(f"Session: {exec_id}\n")

    # Turn 1: 간단한 코드 생성 요청
    q1 = ("dockercoins namespace의 hasher pod 로그에서 '400' 또는 'Empty input' 패턴을 "
          "찾는 bash 스크립트를 만들어줘. 60초 timeout으로 10초마다 체크하고, "
          "발견되면 PASS, 못찾으면 FAIL을 출력해줘.")

    print(f"Q1: {q1}")
    print("=" * 60)
    t0 = time.time()
    try:
        resp = client.ask(exec_id, q1)
        elapsed = time.time() - t0
        text = resp.final_text or resp.raw_text or "(empty)"
        print(f"Time: {elapsed:.1f}s")
        print(f"Response ({len(text)} chars):")
        print(text[:4000])
    except Exception as e:
        elapsed = time.time() - t0
        print(f"ERROR ({elapsed:.1f}s): {type(e).__name__}: {e}")

    print("\n\n")

    # Turn 2: 실행 결과 기반으로 수정 요청
    q2 = ("위 스크립트에 추가로, hasher pod 이름을 자동으로 찾도록 수정하고, "
          "devops-agent-test-hasher-errors CloudWatch alarm 상태가 ALARM으로 바뀌는지도 "
          "확인하는 로직을 추가해줘.")

    print(f"Q2: {q2}")
    print("=" * 60)
    t0 = time.time()
    try:
        resp = client.ask(exec_id, q2)
        elapsed = time.time() - t0
        text = resp.final_text or resp.raw_text or "(empty)"
        print(f"Time: {elapsed:.1f}s")
        print(f"Response ({len(text)} chars):")
        print(text[:4000])
    except Exception as e:
        elapsed = time.time() - t0
        print(f"ERROR ({elapsed:.1f}s): {type(e).__name__}: {e}")


if __name__ == "__main__":
    test_codegen()
