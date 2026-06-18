import boto3, json, os

session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "member1-acc"), region_name=os.environ.get("AWS_REGION", "us-east-1"))
client = session.client('devops-agent')
SPACE_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
TASK_ID = '06c29f57-b992-4d'

# Get full task_id from DynamoDB
table = session.resource('dynamodb').Table('devops-agent-test-investigation-events')
resp = table.scan()
items = sorted(resp.get('Items',[]), key=lambda x: x.get('received_at',''), reverse=True)
TASK_ID = items[0]['task_id']
print(f'Task: {TASK_ID}')

exec_resp = client.list_executions(agentSpaceId=SPACE_ID, taskId=TASK_ID, limit=10)
execs = exec_resp.get('executions', [])
print(f'Executions: {len(execs)}')

for exe in execs:
    exec_id = exe['executionId']
    jr = client.list_journal_records(agentSpaceId=SPACE_ID, executionId=exec_id, limit=100, order='ASC')
    records = jr.get('records', [])
    print(f'Records: {len(records)}')
    
    for i, r in enumerate(records):
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
            text = text.strip()
        except:
            role = '?'
            text = raw[:500]
        
        if role == 'assistant' and text:
            # 코드 관련 키워드 검색
            has_code = any(k in text.lower() for k in ['hasher.py','rng.py','코드','code','def ','lines','파일','snippet','_result_buffer','config/cache','config/response'])
            marker = '💻' if has_code else '  '
            print(f'\n{marker} [{i+1}] {str(r.get("createdAt",""))[:19]}')
            print(f'   {text[:400]}')
