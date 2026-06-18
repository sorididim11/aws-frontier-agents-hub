"""2-Phase scenario generation test.
Phase 1: Generate scenario structure (intents only, no commands)
Phase 2: Ask Agent to fill commands with live infra lookup (same session)
"""
import json
import re
import time

from chat_worker import init_worker

SPACE = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

worker = init_worker(profile="member1-acc", region="us-east-1")
time.sleep(2)

# ====== Phase 1: 시나리오 구조만 생성 (명령 없이) ======
phase1_prompt = """#scenario-generate FM-04

다음 장애 모드에 대한 시나리오 **구조만** 만들어줘. 명령어는 비워두고, 각 단계의 **의도와 설명만** 작성해.

## 장애 모드
- ID: FM-04
- 이름: resource-pressure-eks-node-cpu-stress
- 설명: EKS 노드에 CPU 스트레스를 주입하여 Pod throttling과 서비스 성능 저하 유도
- 트리거: FIS cpu-stress experiment

## 대상 인프라
- EKS 클러스터: devops-agent-test-cluster (primary account)
- 네임스페이스: dockercoins
- 서비스: hasher, worker, rng, webui, redis

## 출력 형식
```json
{
  "id": "FM-04-resource-pressure-eks-node-cpu-stress",
  "target_service": "hasher",
  "trigger": {
    "intent": "FIS cpu-stress template으로 EKS 노드에 CPU 부하 주입",
    "command": ""
  },
  "verification": {
    "steps": [
      {"intent": "단계 설명", "type": "step_type", "command": ""}
    ]
  },
  "restore": {
    "intent": "FIS 실험 중지 및 정상화",
    "command": ""
  }
}
```

명령어(command)는 모두 빈 문자열로 둬. 의도(intent)만 채워.
"""

print("=== Phase 1: 시나리오 구조 생성 ===", flush=True)
t0 = time.time()
resp1 = worker.send_raw(space_id=SPACE, session_id="", prompt=phase1_prompt)
session_id = resp1["session_id"]
reply1 = resp1["reply"]
print(f"Phase 1 완료: {len(reply1)} chars ({time.time()-t0:.0f}s), session={session_id[:16]}")

# JSON 추출
m = re.search(r"```json\s*\n(.*?)```", reply1, re.DOTALL)
if m:
    raw = m.group(1)
    # trailing comma fix
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    structure = json.loads(raw)
    print(f"  trigger.intent = {structure.get('trigger',{}).get('intent','?')[:80]}")
    v_steps = structure.get("verification", {}).get("steps", [])
    print(f"  verification steps: {len(v_steps)}개")
    for i, s in enumerate(v_steps):
        print(f"    [{i}] {s.get('intent', s.get('name','?'))[:60]}")
    print(f"  restore.intent = {structure.get('restore',{}).get('intent','?')[:80]}")
else:
    print("JSON 추출 실패")
    print(reply1[:1500])
    exit(1)

# ====== Phase 2: 같은 세션에서 실제 명령어 생성 ======
print()
print("=== Phase 2: 실시간 인프라 조회 후 명령 생성 ===", flush=True)

steps_desc = []
steps_desc.append(f"trigger: {structure['trigger']['intent']}")
for i, step in enumerate(v_steps):
    intent = step.get("intent", step.get("name", ""))
    stype = step.get("type", "?")
    steps_desc.append(f"verification[{i}] (type={stype}): {intent}")
steps_desc.append(f"restore: {structure['restore']['intent']}")

phase2_prompt = f"""위 시나리오의 각 단계에 대해 실제 실행 가능한 명령어를 만들어줘.

규칙:
- 명령어를 만들기 전에 반드시 실제 인프라를 조회해서 정확한 리소스 ID, 이름, 태그 확인
- FIS template은 target filter가 현재 노드그룹과 일치하는 것만 사용
- 알람 이름은 실제 존재하는 것만 사용
- kubectl 명령은 현재 클러스터에서 실행 가능한 것만

채워야 할 단계:
{chr(10).join(f"- {s}" for s in steps_desc)}

응답 형식 (```json 블록):
```json
{{
  "trigger": {{"command": "실제 AWS CLI 또는 kubectl 명령"}},
  "verification_commands": ["step0 명령", "step1 명령", ...],
  "restore": {{"command": "실제 복원 명령"}}
}}
```
"""

t1 = time.time()
resp2 = worker.send_raw(space_id=SPACE, session_id=session_id, prompt=phase2_prompt)
reply2 = resp2["reply"]
print(f"Phase 2 완료: {len(reply2)} chars ({time.time()-t1:.0f}s)")
print()

# JSON 추출
m2 = re.search(r"```json\s*\n(.*?)```", reply2, re.DOTALL)
if m2:
    raw2 = m2.group(1)
    raw2 = re.sub(r",\s*([}\]])", r"\1", raw2)
    commands = json.loads(raw2)
    print("=== 생성된 명령어 ===")
    print(f"  trigger: {commands.get('trigger',{}).get('command','?')[:120]}")
    v_cmds = commands.get("verification_commands", [])
    for i, cmd in enumerate(v_cmds):
        if isinstance(cmd, str):
            print(f"  verify[{i}]: {cmd[:120]}")
        elif isinstance(cmd, dict):
            print(f"  verify[{i}]: {json.dumps(cmd, ensure_ascii=False)[:120]}")
        else:
            print(f"  verify[{i}]: {str(cmd)[:120]}")
    print(f"  restore: {commands.get('restore',{}).get('command','?')[:120]}")
else:
    print("Phase 2 JSON 추출 실패")
    print(reply2[:2000])

print()
print("=" * 60)
print("완료")
