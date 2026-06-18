import urllib.request, json

TASK_ID = "90732bc3-f132-4748-95bb-0588cdca68d5"
url = f"http://localhost:8081/api/investigation-journal?task_id={TASK_ID}&analyze=false&model=haiku"
resp = urllib.request.urlopen(url, timeout=30)
d = json.loads(resp.read())

print(f"raw: {len(d.get('raw_messages',[]))} classified: {len(d.get('classified',[]))}")
for g in d.get('classified',[]):
    print(f"\n{g['icon']} {g['type']} ({g['count']}건)")
    for m in g['messages']:
        ds = m.get('data_sources','')
        print(f"  {ds} [{m['time'][11:19]}] {m['summary']}")

print("\n=== 코드 관련 메시지 (Skill 효과) ===")
for m in d.get('raw_messages',[]):
    t = m['text'].lower()
    if any(k in t for k in ['hasher.py','rng.py','코드','code','def ','lines','파일','snippet']):
        print(f"[{m['time'][11:19]}] {m['text'][:400]}")
        print()
