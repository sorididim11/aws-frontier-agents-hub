#!/usr/bin/env python3
"""Agent에게 코드 접근 방식을 확인하는 테스트.

질문: "너 방금 hasher 코드를 어떻게 읽은거야? kubectl exec? git repo? 어떤 방법?"
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))

import boto3
from chat_worker import init_worker
from arch_analysis import AgentChatClient

SPACE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PROFILE = "member1-acc"

QUESTION = """너는 코드 소스를 읽을 때 어떤 방식으로 접근하는거야?
1) kubectl exec로 pod 내부 파일시스템을 읽는건지
2) 연결된 GitHub repository에서 읽는건지
3) 다른 방법이 있는건지

그리고 이 Agent Space에 GitHub repository가 연결되어 있어?
연결된 소스 코드 저장소 목록을 알려줘."""


def main():
    init_worker(profile=PROFILE, region="us-east-1")
    client = AgentChatClient(SPACE_ID)
    exec_id = client.create_session()
    print(f"Session: {exec_id}\n")
    print(f"Q: {QUESTION}\n{'='*70}")

    t0 = time.time()
    try:
        resp = client.ask(exec_id, QUESTION)
        elapsed = time.time() - t0
        text = resp.final_text or resp.raw_text or "(empty)"
        print(f"Time: {elapsed:.1f}s")
        print(f"Response ({len(text)} chars):\n")
        print(text)
    except Exception as e:
        elapsed = time.time() - t0
        print(f"ERROR ({elapsed:.1f}s): {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
