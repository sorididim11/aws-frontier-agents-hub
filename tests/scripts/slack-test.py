#!/usr/bin/env python3
import urllib.request, json, sys

token = ""
channel = ""
try:
    import boto3
    sm = boto3.client("secretsmanager", region_name="us-east-1")
    resp = sm.get_secret_value(SecretId="devops-agent-test-slack-bot-token")
    secret = json.loads(resp["SecretString"])
    token = secret.get("bot_token", "")
    channel = secret.get("channel_id", "")
    print("Secret loaded OK")
except Exception as e:
    print("Secret load error:", e)
    sys.exit(1)

if not token or not channel:
    print("No token/channel")
    sys.exit(1)

url = "https://slack.com/api/conversations.history?channel=" + channel + "&limit=5"
req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    print("Slack API ok:", data.get("ok"))
    if not data.get("ok"):
        print("Error:", data.get("error", "unknown"))
        sys.exit(1)
    msgs = data.get("messages", [])
    print("Message count:", len(msgs))
    for m in msgs[:3]:
        text = m.get("text", "")[:100]
        ts = m.get("ts", "")
        bot = m.get("bot_id", "")
        print("  [" + ts + "] bot=" + str(bot) + " | " + text)
except Exception as e:
    print("HTTP error:", e)
    sys.exit(1)
