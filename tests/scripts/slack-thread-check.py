#!/usr/bin/env python3
"""Check Slack messages and thread replies to diagnose investigation message visibility."""
import urllib.request
import json
import os
import sys

# Use profile for local execution
import boto3
session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "member1-acc"), region_name=os.environ.get("AWS_REGION", "us-east-1"))
sm = session.client("secretsmanager")
resp = sm.get_secret_value(SecretId="devops-agent-test-slack-bot-token")
secret = json.loads(resp["SecretString"])
token = secret["bot_token"]
channel = secret["channel_id"]
print("Channel:", channel)
print("Token prefix:", token[:15] + "...")
print()

# Fetch recent messages
url = "https://slack.com/api/conversations.history?channel=" + channel + "&limit=10"
req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read().decode())

if not data.get("ok"):
    print("ERROR:", data.get("error"))
    sys.exit(1)

msgs = data.get("messages", [])
print("=== Top-level messages:", len(msgs), "===")
for i, m in enumerate(msgs):
    ts = m.get("ts", "")
    rc = m.get("reply_count", 0)
    thread_ts = m.get("thread_ts", "")
    bot_id = m.get("bot_id", "")
    text = m.get("text", "")[:120]
    print(f"\n[{i}] ts={ts} reply_count={rc} bot_id={bot_id}")
    print(f"    text: {text}")

    # Fetch thread replies if any
    if rc and rc > 0:
        rurl = (
            "https://slack.com/api/conversations.replies?channel="
            + channel + "&ts=" + ts + "&limit=5"
        )
        rreq = urllib.request.Request(
            rurl, headers={"Authorization": "Bearer " + token}
        )
        with urllib.request.urlopen(rreq, timeout=15) as rr:
            rdata = json.loads(rr.read().decode())
        if rdata.get("ok"):
            replies = rdata.get("messages", [])
            print(f"    --- Thread has {len(replies)} messages (incl parent) ---")
            for rep in replies[1:4]:
                rtext = rep.get("text", "")[:150]
                print(f"    reply [{rep.get('ts','')}]: {rtext}")
        else:
            print(f"    --- Thread fetch error: {rdata.get('error')} ---")

print("\n=== Done ===")
