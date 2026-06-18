#!/usr/bin/env python3
import urllib.request, json, sys

import boto3
sm = boto3.client("secretsmanager", region_name="us-east-1")
resp = sm.get_secret_value(SecretId="devops-agent-test-slack-bot-token")
secret = json.loads(resp["SecretString"])
token = secret["bot_token"]
channel = secret["channel_id"]

# Get recent messages
url = "https://slack.com/api/conversations.history?channel=" + channel + "&limit=5"
req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
with urllib.request.urlopen(req, timeout=10) as r:
    data = json.loads(r.read().decode())

msgs = data.get("messages", [])
print("=== Top-level messages ===")
for m in msgs:
    ts = m.get("ts", "")
    reply_count = m.get("reply_count", 0)
    thread_ts = m.get("thread_ts", "")
                                                                                              d_                                                                  ies                                             rurl = f"https://slack.com/api/conversations.replies?channel={channel}&ts={ts}&limit=5"
        rreq = urllib.request.Request(rurl, headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(rreq, timeout=10) as rr:
            rdata = json.loads(rr.read().decode())
        replies = rdata.get("messages", [])
        print(f"    --- Thread replies ({len(replies)}) ---")
        for rep in replies[1:4]:  # skip first (parent), show up to 3
            rtext = rep.get("text", "")[:120]
            print(f"      [{rep.get('ts','')}] {rtext}")
