#!/usr/bin/env python3
"""DevOps Agent 채팅 capability 테스트.

Read 작업과 Write 작업을 각각 테스트하여 Agent가 실제로 무엇을 할 수 있는지 확인.
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(__file__))

import boto3
from arch_analysis import AgentChatClient

SPACE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PROFILE = "member1-acc"

def test_chat():
    session = boto3.Session(profile_name=PROFILE, region_name="us-east-1")
    client = AgentChatClient(SPACE_ID, session)
    exec_id = client.create_session()
    print(f"Session: {exec_id}\n")

    tests = [
        # --- READ operations ---
        ("READ: pod 목록", "dockercoins namespace의 pod 목록을 보여줘. kubectl get pods -n dockercoins 결과를 알려줘."),
        ("READ: deployment 상세", "dockercoins namespace의 hasher deployment 상세 정보를 알려줘. replicas, image 등."),
        ("READ: CloudWatch alarm 목록", "devops-agent-test- 로 시작하는 CloudWatch alarm 목록과 각 상태를 알려줘."),
        ("READ: 서비스 확인", "dockercoins namespace의 service 목록과 각 endpoint를 알려줘."),
    ]

    for label, question in tests:
        print(f"{'='*60}")
        print(f"TEST: {label}")
        print(f"Q: {question[:80]}...")
        print(f"{'='*60}")
        t0 = time.time()
        try:
            resp = client.ask(exec_id, question)
            elapsed = time.time() - t0
            text = resp.final_text or resp.raw_text or "(empty)"
            print(f"Time: {elapsed:.1f}s")
            print(f"Response ({len(text)} chars):")
            print(text[:2000])
            if resp.parsed_json:
                print(f"\nParsed JSON: {json.dumps(resp.parsed_json, indent=2, ensure_ascii=False)[:500]}")
        except Exception as e:
            elapsed = time.time() - t0
            print(f"ERROR ({elapsed:.1f}s): {type(e).__name__}: {e}")
        print()

if __name__ == "__main__":
    test_chat()
