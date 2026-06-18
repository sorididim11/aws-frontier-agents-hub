import boto3, json, os

session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "member1-acc"), region_name=os.environ.get("AWS_REGION", "us-east-1"))
client = session.client('devops-agent')
SPACE_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
TASK_ID = 'd1f656a7-f577-49a5-990f-64f1ca8ce939'

exec_resp = client.list_executions(agentSpaceId=SPACE_ID, taskId=TASK_ID, limit=10)
execs = exec_resp.get('executions', [])
print(f'Executions: {len(execs)}')
for e in execs:
    print(f'  {e["executionId"][:30]} | {e["executionStatus"]} | {e.get("agentSubTask","")}')

if not execs:
    exit()

exec_id = execs[0]['executionId']
jr = client.list_journal_records(agentSpaceId=SPACE_ID, executionId=exec_id, limit=100, order='ASC')
records = jr.get('records', [])
print(f'\nRecords: {len(records)}')

for i, r in enumerate(records):
    rt = r.get('recordType','')
    content = r.get('content', {})
    raw = content.get('text','') if isinstance(content, dict) else str(content)
    try:
        msg = json.loads(raw)
        role = msg.get('role','')
        parts = msg.get('content', [])
        text = ''
        for p in (parts if isinstance(parts, list) else []):
            if isinstance(p, dict) and p.get('text'):
                text += p['text'] + ' '
        text = text.strip()[:400]
    except:
        role = '?'
        text = raw[:400]
    if role == 'assistant' and text:
        print(f'\n[{i+1}] type={rt} | {str(r.get("createdAt",""))[:19]}')
        print(f'  {text}')
