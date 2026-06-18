#!/usr/bin/env python3
"""DevOps Agent 채팅 write 작업 테스트.

Agent가 실제로 kubectl delete, scale, FIS 실행 등 write 작업을 할 수 있는지 확인.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))

import boto3
from arch_analysis import AgentChatClient

SPACE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PROFILE = "member1-acc"

def test_write():
    session = boto3.Session(profile_name=PROFILE, region_name="us-east-1")
    client = AgentChatClient(SPACE_ID, session)
    exec_id = client.create_session()
    print(f"Session: {exec_id}\n")

    tests = [
        ("WRITE: pod 삭제 시도",
         "dockercoins namespace에서 hasher pod를 삭제해줘. kubectl delete pod -n dockercoins -l app=hasher 실행해줘."),
        ("WRITE: replica scale 시도",
         "dockercoins namespace의 hasher deployment를 3 replica로 scale해줘. kubectl scale deploy hasher -n dockercoins --replicas=3"),
        ("WRITE: alarm 상태 변경 시도",
         "devops-agent-test-hasher-oomkilled alarm을 OK 상태로 리셋해줘. aws cloudwatch set-alarm-state 명령으로."),
    ]

    for label, question in tests:
        print(f"{'='*60}")
        print(f"TEST: {label}")
        print(f"Q: {question[:100]}...")
        print(f"{'='*60}")
        t0 = time.time()
        try:
            resp = client.ask(exec_id, question)
            elapsed = time.time() - t0
            text = resp.final_text or resp.raw_text or "(empty)"
            print(f"Time: {elapsed:.1f}s")
            print(f"Response ({len(text)} chars):")
            print(text[:2000])
        except Exception as e:
            elapsed = time.time() - t0
            print(f"ERROR ({elapsed:.1f}s): {type(e).__name__}: {e}")
        print()

if __name__ == "__main__":
    test_write()
