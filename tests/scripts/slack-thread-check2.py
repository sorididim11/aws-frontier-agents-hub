#!/usr/bin/env python3
"""Check Slack messages and thread replies - uses token from env."""
import urllib.request
import json
import os
import sys

token = os.environ.get("SLACK_TOKEN", "")
channel = os.environ.get("SLACK_CHANNEL", "")
if not token or not channel:
    print("Set SLACK_TOKEN and SLACK_CHANNEL env vars")
    sys.exit(1)

print("Channel:", channel)
print()

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
    bot_id = m.get("bot_id", "")
    text = m.get("text", "")[:120]
    print()
    print("[%d] ts=%s reply_count=%d bot=%s" % (i, ts, rc, bot_id))
    print("    %s" % text)

    if rc > 0:
        rurl = ("https://slack.com/api/conversations.replies?channel="
                + channel + "&ts=" + ts + "&limit=5")
        rreq = urllib.request.Request(rurl, headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(rreq, timeout=15) as rr:
            rdata = json.loads(rr.read().decode())
        if rdata.get("ok"):
            replies = rdata.get("messages", [])
            print("    --- Thread: %d messages (incl parent) ---" % len(replies))
            for rep in replies[1:4]:
                rtext = rep.get("text", "")[:150]
                print("    reply [%s]: %s" % (rep.get("ts", ""), rtext))
        else:
            print("    --- Thread error: %s ---" % rdata.get("error"))

print()
print("=== Done ===")
