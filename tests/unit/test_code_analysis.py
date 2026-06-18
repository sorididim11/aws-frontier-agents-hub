#!/usr/bin/env python3
"""DevOps Agent에게 서비스 소스코드 구조 분석을 요청하는 테스트.

목표: Agent가 kubectl exec로 컨테이너 내 소스를 읽고,
모듈/클래스/함수 구조를 JSON으로 반환할 수 있는지 검증.
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(__file__))

import boto3
from arch_analysis import AgentChatClient

SPACE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PROFILE = "member1-acc"

CODE_ANALYSIS_PROMPT = """dockercoins namespace의 hasher 서비스 소스코드를 분석해줘.

1단계: hasher pod에 접속해서 소스코드 파일 목록을 확인해줘.
  - kubectl exec 로 pod 내부의 /app 또는 소스가 있을만한 경로를 탐색
  - 어떤 언어로 작성되었는지, 주요 파일이 뭔지 확인

2단계: 주요 소스 파일을 읽고 구조를 분석해줘.
  - 모듈(파일) 간 import/require 관계
  - 클래스 또는 주요 함수 목록
  - 외부 연동 (HTTP endpoint, DB 연결 등)

결과를 다음 JSON 형식으로 반환해줘:

```json
{
  "service_name": "hasher",
  "language": "ruby/python/go 등",
  "source_path": "/app 등 실제 경로",
  "files": [
    {
      "path": "hasher.rb",
      "type": "main/module/config",
      "classes": ["ClassName"],
      "functions": ["func1", "func2"],
      "imports": ["sinatra", "digest"],
      "endpoints": [{"method": "POST", "path": "/", "description": "해시 생성"}],
      "external_calls": []
    }
  ],
  "dependencies": [
    {"from": "file_a", "to": "file_b", "type": "import/call"}
  ]
}
```
"""


def test_code_analysis():
    from chat_worker import init_worker
    init_worker(profile=PROFILE, region="us-east-1")

    session = boto3.Session(profile_name=PROFILE, region_name="us-east-1")
    client = AgentChatClient(SPACE_ID, session)
    exec_id = client.create_session()
    print(f"Session: {exec_id}\n")

    print("=" * 70)
    print("TEST: Agent 소스코드 구조 분석 (hasher)")
    print("=" * 70)
    t0 = time.time()
    try:
        resp = client.ask(exec_id, CODE_ANALYSIS_PROMPT)
        elapsed = time.time() - t0
        text = resp.final_text or resp.raw_text or "(empty)"
        print(f"Time: {elapsed:.1f}s")
        print(f"Response ({len(text)} chars):")
        print(text[:5000])
        if resp.parsed_json:
            print(f"\n{'='*40} PARSED JSON {'='*40}")
            print(json.dumps(resp.parsed_json, indent=2, ensure_ascii=False)[:3000])
        else:
            print("\n[WARNING] JSON 파싱 실패 — Agent 응답에서 JSON 블록을 찾지 못함")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"ERROR ({elapsed:.1f}s): {type(e).__name__}: {e}")


if __name__ == "__main__":
    test_code_analysis()
