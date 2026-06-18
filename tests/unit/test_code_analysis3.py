#!/usr/bin/env python3
"""Agent에게 GitHub repo에서 직접 코드를 읽어 분석하라고 요청."""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(__file__))

import boto3
from chat_worker import init_worker
from arch_analysis import AgentChatClient

SPACE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PROFILE = "member1-acc"

QUESTION = """연결된 GitHub repository (sorididim11/frontier-devops-agent-test-app)에서
hasher 서비스의 소스코드를 직접 읽어서 분석해줘.

repo의 디렉토리 구조를 먼저 확인하고, hasher 관련 소스 파일을 찾아서 읽어줘.
그리고 분석 결과를 다음 JSON으로 반환해줘:

```json
{
  "access_method": "github_repo 또는 kubectl_exec 중 실제 사용한 방법",
  "repo_path": "repo 내에서 hasher 소스 위치",
  "files_found": ["파일목록"],
  "service_name": "hasher",
  "language": "언어",
  "modules": [
    {
      "file": "파일명",
      "imports": ["의존성"],
      "functions": ["함수명"],
      "classes": ["클래스명"],
      "endpoints": [{"method": "GET/POST", "path": "/경로"}]
    }
  ]
}
```"""


def main():
    init_worker(profile=PROFILE, region="us-east-1")
    client = AgentChatClient(SPACE_ID)
    exec_id = client.create_session()
    print(f"Session: {exec_id}\n")
    print(f"Q: {QUESTION[:100]}...\n{'='*70}")

    t0 = time.time()
    try:
        resp = client.ask(exec_id, QUESTION)
        elapsed = time.time() - t0
        text = resp.final_text or resp.raw_text or "(empty)"
        print(f"Time: {elapsed:.1f}s")
        print(f"Response ({len(text)} chars):\n")
        print(text[:5000])
        if resp.parsed_json:
            print(f"\n{'='*40} PARSED JSON {'='*40}")
            print(json.dumps(resp.parsed_json, indent=2, ensure_ascii=False)[:3000])
    except Exception as e:
        elapsed = time.time() - t0
        print(f"ERROR ({elapsed:.1f}s): {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
