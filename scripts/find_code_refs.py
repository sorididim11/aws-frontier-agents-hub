import boto3, json, os
session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "member1-acc"), region_name=os.environ.get("AWS_REGION", "us-east-1"))
client = session.client('devops-agent')
SPACE_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
TASK_ID = '06c29f57-b992-4d9e-807f-7264f5afd561'
exec_resp = client.list_executions(agentSpaceId=SPACE_ID, taskId=TASK_ID, limit=1)
exec_id = exec_resp['executions'][0]['executionId']
jr = client.list_journal_records(agentSpaceId=SPACE_ID, executionId=exec_id, limit=100, order='ASC')
keywords = ['hasher.py','_result_buffer','config/cache','aggressive_cache','cache_chunk','코드 분석','source code']
for i, r in enumerate(jr['records']):
    content = r.get('content',{})
    raw = content.get('text','') if isinstance(content, dict) else str(content)
    for kw in keywords:
        if kw.lower() in raw.lower():
            print(f'[{i+1}] type={r.get("recordType","")} keyword={kw}')
            print(f'  {raw[:600]}')
            print()
            break
