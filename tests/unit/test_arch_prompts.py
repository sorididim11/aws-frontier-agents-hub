#!/usr/bin/env python3
"""에이전트 프롬프트 단위 테스트 하네스.

에이전트의 프롬프트를 반복 수정하면서 그룹 분류 품질을 검증한다.
Flask/DynamoDB 의존 없이 standalone으로 실행.

모드:
  --mode live     실제 DevOps Agent 인터뷰 + fixture 저장
  --mode replay   저장된 fixture로 프롬프트만 교체하여 빠른 반복

사용법:
  # 1. fixture 수집 (최초 1회)
  AWS_PROFILE=member1-acc python3 test_arch_prompts.py --mode live --layers L1 --save-fixture

  # 2. 프롬프트 반복 테스트
  python3 test_arch_prompts.py --mode replay --layers L1 --fixture fixtures/arch_qa_L1_*.json

  # 3. 결과 비교
  python3 test_arch_prompts.py --compare results/v1.json results/v2.json
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

SPACE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "test_results")

T0 = time.time()


def elapsed():
    return f"{time.time() - T0:6.1f}s"


# ══════════════════════════════════════════════════════════════════
# ReplayChatClient — fixture에서 DevOps Agent 답변을 재생
# ══════════════════════════════════════════════════════════════════

class ReplayChatClient:
    """AgentChatClient 대체 — 녹화된 인터뷰 답변을 반환."""

    def __init__(self, interviews: list):
        self._interviews = interviews
        self._used = set()
        self._call_count = 0

    def create_session(self) -> str:
        return "replay-session"

    def ask(self, execution_id: str, question: str):
        from arch_analysis import ChatResponse, ChatBlock
        self._call_count += 1
        best_idx, best_score = self._find_best_match(question)
        if best_score > 0.2 and best_idx not in self._used:
            recorded = self._interviews[best_idx]
            self._used.add(best_idx)
            answer = recorded.get("answer", "")
            print(f"  [REPLAY] Q: {question[:80]}...")
            print(f"  [REPLAY] → matched interview[{best_idx}] (score={best_score:.2f})")
        else:
            answer = "I don't have specific information about that topic."
            print(f"  [REPLAY] Q: {question[:80]}...")
            print(f"  [REPLAY] → NO MATCH (best_score={best_score:.2f})")

        block = ChatBlock(index=0, block_type="final_response", text=answer)
        resp = ChatResponse(question=question, blocks=[block])
        resp.raw_text = answer
        return resp

    def _find_best_match(self, question: str) -> tuple:
        q_words = set(self._tokenize(question))
        best_idx, best_score = 0, 0.0
        for i, rec in enumerate(self._interviews):
            rec_q = rec.get("question", "")
            rec_a = rec.get("answer", "")
            r_words = set(self._tokenize(rec_q)) | set(self._tokenize(rec_a[:200]))
            if not q_words or not r_words:
                continue
            overlap = len(q_words & r_words)
            score = overlap / max(len(q_words | r_words), 1)
            if score > best_score:
                best_score = score
                best_idx = i
        return best_idx, best_score

    @staticmethod
    def _tokenize(text: str) -> list:
        return [w.lower() for w in re.findall(r'[a-zA-Z0-9_-]+', text) if len(w) > 2]

    def stats(self) -> dict:
        return {
            "total_calls": self._call_count,
            "matched": len(self._used),
            "unmatched": self._call_count - len(self._used),
            "fixture_size": len(self._interviews),
            "unused_fixtures": len(self._interviews) - len(self._used),
        }


# ══════════════════════════════════════════════════════════════════
# RecordingChatClient — 라이브 Q&A를 녹화
# ══════════════════════════════════════════════════════════════════

class RecordingChatClient:
    """AgentChatClient 래퍼 — 실제 호출하면서 Q&A를 녹화."""

    def __init__(self, real_client):
        self._real = real_client
        self.recordings: list = []

    def create_session(self) -> str:
        return self._real.create_session()

    def ask(self, execution_id: str, question: str):
        resp = self._real.ask(execution_id, question)
        self.recordings.append({
            "turn": len(self.recordings) + 1,
            "question": question,
            "answer": resp.final_text,
            "tool_calls": [str(tc)[:300] for tc in resp.tool_calls[:5]],
        })
        return resp


# ══════════════════════════════════════════════════════════════════
# 스코어링
# ══════════════════════════════════════════════════════════════════

def score_grouping(actual_services: list, expected: dict) -> dict:
    """에이전트 L1 출력의 그룹 분류를 채점."""
    expected_groups = expected.get("expected_groups", {})
    forbidden = set(expected.get("forbidden_group_names", []))
    weights = expected.get("scoring_weights", {
        "group_name_quality": 0.4, "member_assignment": 0.4, "coverage": 0.2,
    })
    aliases = expected.get("group_aliases", {})

    actual_groups = {}
    for svc in actual_services:
        g = svc.get("group", "") or "NONE"
        actual_groups.setdefault(g, []).append(svc.get("name", "?"))

    # 1. 그룹 이름 품질 — 기대 그룹과 매칭 (aliases 포함)
    name_matches = 0
    name_total = len(expected_groups)
    matched_expected = set()
    group_name_map = {}
    for actual_name in actual_groups:
        for exp_name in expected_groups:
            if _is_similar_group(actual_name, exp_name, aliases.get(exp_name, [])):
                name_matches += 1
                matched_expected.add(exp_name)
                group_name_map[actual_name] = exp_name
                break
    name_score = name_matches / max(name_total, 1)

    # 2. 멤버 할당 정확도
    correct_assignments = 0
    total_services = 0
    member_details = []
    for exp_group, exp_members in expected_groups.items():
        for member in exp_members:
            total_services += 1
            actual_group = _find_group_for_service(actual_groups, member)
            matched_exp = group_name_map.get(actual_group, "")
            is_correct = matched_exp == exp_group
            if is_correct:
                correct_assignments += 1
            member_details.append({
                "service": member,
                "expected_group": exp_group,
                "actual_group": actual_group or "(missing)",
                "correct": is_correct,
            })
    member_score = correct_assignments / max(total_services, 1)

    # 3. 커버리지
    coverage_score = len(matched_expected) / max(len(expected_groups), 1)

    # 4. forbidden 이름 체크
    forbidden_found = [n for n in actual_groups if n in forbidden]

    # 종합 점수
    total = (
        name_score * weights.get("group_name_quality", 0.4)
        + member_score * weights.get("member_assignment", 0.4)
        + coverage_score * weights.get("coverage", 0.2)
    )
    penalty = len(forbidden_found) * 0.1
    total = max(0, total - penalty)

    return {
        "total_score": round(total * 100, 1),
        "name_score": round(name_score * 100, 1),
        "member_score": round(member_score * 100, 1),
        "coverage_score": round(coverage_score * 100, 1),
        "forbidden_found": forbidden_found,
        "actual_groups": {k: sorted(v) for k, v in actual_groups.items()},
        "expected_groups": {k: sorted(v) for k, v in expected_groups.items()},
        "group_name_map": group_name_map,
        "member_details": member_details,
    }


def _is_similar_group(actual: str, expected: str, aliases: list = None) -> bool:
    def _normalize(s):
        return s.lower().replace("-", "").replace("_", "").replace(" ", "")

    a = _normalize(actual)
    e = _normalize(expected)
    if a == e:
        return True
    if a in e or e in a:
        return True

    for alias in (aliases or []):
        al = _normalize(alias)
        if a == al or a in al or al in a:
            return True

    a_words = set(re.findall(r'[a-z]+', a))
    e_words = set(re.findall(r'[a-z]+', e))
    overlap = len(a_words & e_words)
    return overlap >= 1 and overlap / max(len(a_words | e_words), 1) > 0.4


def _normalize_service_name(s: str) -> str:
    s = s.lower()
    s = re.sub(r'[()\/,]', ' ', s)
    s = re.sub(r'\s+', '-', s.strip())
    s = s.replace("_", "-")
    s = re.sub(r'-+', '-', s).strip('-')
    return s


def _service_words(s: str) -> set:
    stop = {"amazon", "aws", "the", "a", "an", "app"}
    return {w for w in re.findall(r'[a-z0-9]+', _normalize_service_name(s)) if w not in stop and len(w) > 1}


def _find_group_for_service(groups: dict, service_name: str) -> str:
    sn = _normalize_service_name(service_name)
    for group_name, members in groups.items():
        if service_name in members:
            return group_name
        for m in members:
            mn = _normalize_service_name(m)
            if sn == mn:
                return group_name
    for group_name, members in groups.items():
        for m in members:
            mn = _normalize_service_name(m)
            if sn in mn or mn in sn:
                return group_name
    sn_words = _service_words(service_name)
    if not sn_words:
        return ""
    best_group, best_score = "", 0.0
    for group_name, members in groups.items():
        for m in members:
            mn_words = _service_words(m)
            if not mn_words:
                continue
            overlap = len(sn_words & mn_words)
            score = overlap / max(len(sn_words | mn_words), 1)
            if score > best_score:
                best_score = score
                best_group = group_name
    return best_group if best_score >= 0.3 else ""


# ══════════════════════════════════════════════════════════════════
# 결과 출력
# ══════════════════════════════════════════════════════════════════

def print_score(score: dict):
    print(f"\n{'='*60}")
    print(f"  그룹 분류 품질 점수: {score['total_score']}/100")
    print(f"{'='*60}")
    print(f"  이름 품질:    {score['name_score']:5.1f}  (기대 그룹명과 매칭)")
    print(f"  멤버 할당:    {score['member_score']:5.1f}  (서비스→그룹 정확도)")
    print(f"  커버리지:     {score['coverage_score']:5.1f}  (기대 그룹 존재 비율)")
    if score["forbidden_found"]:
        print(f"  ⚠ forbidden: {score['forbidden_found']}")
    print()

    print("  실제 그룹:")
    for g, members in sorted(score["actual_groups"].items()):
        mapped = score["group_name_map"].get(g, "")
        tag = f" → {mapped}" if mapped else " ✗"
        print(f"    [{g}]{tag}: {', '.join(members)}")
    print()

    print("  기대 그룹:")
    for g, members in sorted(score["expected_groups"].items()):
        print(f"    [{g}]: {', '.join(members)}")
    print()

    wrong = [d for d in score["member_details"] if not d["correct"]]
    if wrong:
        print("  오분류된 서비스:")
        for d in wrong:
            print(f"    {d['service']}: {d['actual_group']} (기대: {d['expected_group']})")
    print()


def print_comparison(result1: dict, result2: dict):
    s1 = result1["score"]
    s2 = result2["score"]
    print(f"\n{'='*60}")
    print(f"  비교: {result1.get('label','v1')} vs {result2.get('label','v2')}")
    print(f"{'='*60}")
    print(f"  {'항목':<16} {'v1':>8} {'v2':>8} {'변화':>8}")
    print(f"  {'-'*40}")
    for key in ["total_score", "name_score", "member_score", "coverage_score"]:
        v1, v2 = s1[key], s2[key]
        delta = v2 - v1
        sign = "+" if delta > 0 else ""
        print(f"  {key:<16} {v1:>7.1f} {v2:>7.1f} {sign}{delta:>7.1f}")
    print()


# ══════════════════════════════════════════════════════════════════
# 실행 모드
# ══════════════════════════════════════════════════════════════════

def run_live(args):
    """라이브 모드 — 코드가 Q1 전송, Sonnet이 파싱 + fixture 저장."""
    import boto3
    from arch_analysis import (
        ArchitectAgent, AgentChatClient, ArchitectureAgentDiscoverer,
        load_questions,
    )

    profile = os.environ.get("AWS_PROFILE", "member1-acc")
    session = boto3.Session(profile_name=profile, region_name="us-east-1")

    sts = session.client("sts")
    identity = sts.get_caller_identity()
    print(f"AWS Account: {identity['Account']}")

    from botocore.config import Config as BotoConfig
    bedrock = session.client(
        "bedrock-runtime",
        config=BotoConfig(read_timeout=300, connect_timeout=10),
    )

    chat_client = AgentChatClient(SPACE_ID, session)
    recording_client = RecordingChatClient(chat_client)

    target_layers = [l.strip() for l in args.layers.split(",")]
    q_config = load_questions()

    tagged_summary = ""
    try:
        from overview_app import _get_aws_associations, _session_for_association, _fetch_tagged_resources
        tagged = {}
        aws_assocs = _get_aws_associations(SPACE_ID)
        for assoc in aws_assocs:
            acct = assoc["account_id"]
            sess = _session_for_association(assoc)
            total, by_service = _fetch_tagged_resources("App", session=sess)
            tagged[acct] = {"total": total, "by_service": by_service, "ok": True}
        tagged_summary = ArchitectureAgentDiscoverer._summarize_tagged(tagged)
    except Exception as e:
        print(f"tagged resources 수집 실패 (계속 진행): {e}")

    results_by_layer = {}
    fixtures_by_layer = {}

    prev_context = None
    for layer in target_layers:
        print(f"\n[{elapsed()}] === {layer} 라이브 실행 (fixed-question) ===")

        exec_id = recording_client.create_session()
        recording_client.recordings = []

        agent_cfg = q_config["agents"].get(layer, {})
        system_prompt = agent_cfg.get("system_prompt", "")
        if tagged_summary:
            system_prompt += "\n\n## 사전 정보 — 알려진 AWS 리소스\n" + tagged_summary

        # Step 1: Build fixed question from template
        disc = ArchitectureAgentDiscoverer.__new__(ArchitectureAgentDiscoverer)
        fixed_question = disc._build_question(layer, tagged_summary, prev_context)
        devops_answer = None

        if fixed_question:
            print(f"  [Q1-FIXED] ({len(fixed_question)} chars)")
            print(f"  {fixed_question[:200]}...")
            t0 = time.time()
            resp = recording_client.ask(exec_id, fixed_question)
            devops_answer = resp.final_text
            print(f"  [A1] {len(devops_answer)} chars, {time.time()-t0:.1f}s")

        # Step 2: Pass answer to Sonnet for parsing
        def on_event(event):
            t = event.get("type", "")
            if t == "agent_question":
                print(f"  [Q-FOLLOWUP] {event.get('question','')[:120]}...")
            elif t == "agent_answer":
                print(f"  [A-FOLLOWUP] {len(event.get('answer',''))}자")
            elif t == "agent_evaluation":
                print(f"  [EVAL] score={event.get('score')}, verdict={event.get('verdict')}")
            elif t == "agent_thinking":
                print(f"  [THINK] {event.get('thought','')[:100]}...")

        agent = ArchitectAgent(
            agent_type=layer, bedrock_client=bedrock,
            chat_client=recording_client, execution_id=exec_id,
            on_event=on_event, model_id=args.model or agent_cfg.get("model_id", "us.anthropic.claude-sonnet-4-6"),
            max_turns=args.max_turns,
            quality_threshold=agent_cfg.get("quality_threshold", 75),
            system_prompt=system_prompt,
        )

        layer_result = agent.run(context=prev_context, devops_answer=devops_answer)
        results_by_layer[layer] = layer_result or {}
        fixtures_by_layer[layer] = list(recording_client.recordings)
        prev_context = layer_result

        print(f"[{elapsed()}] {layer} 완료: {len(recording_client.recordings)} Q&A")

    # fixture 저장
    if args.save_fixture:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        layers_tag = "_".join(target_layers)
        fixture_path = os.path.join(FIXTURES_DIR, f"arch_qa_{layers_tag}_{ts}.json")
        fixture_data = {
            "meta": {
                "captured_at": datetime.now().isoformat(),
                "space_id": SPACE_ID,
                "layers": target_layers,
                "model": args.model or "default",
            },
            "layers": {},
        }
        for layer in target_layers:
            fixture_data["layers"][layer] = {
                "interview": fixtures_by_layer.get(layer, []),
                "submitted_result": results_by_layer.get(layer, {}),
            }
        with open(fixture_path, "w", encoding="utf-8") as f:
            json.dump(fixture_data, f, indent=2, ensure_ascii=False)
        print(f"\n[FIXTURE] 저장됨: {fixture_path}")

    # 스코어링
    if "L1" in target_layers and results_by_layer.get("L1"):
        expected = _load_expected(args.expected)
        services = results_by_layer["L1"].get("services", [])
        score = score_grouping(services, expected)
        print_score(score)
        _save_result(target_layers, results_by_layer, score, "live", args)


def run_replay(args):
    """리플레이 모드 — fixture의 Q1 답변을 Sonnet에 직접 전달하여 파싱 테스트."""
    import boto3
    from arch_analysis import ArchitectAgent, load_questions

    if not args.fixture:
        print("ERROR: --fixture 필요")
        sys.exit(1)

    with open(args.fixture, encoding="utf-8") as f:
        fixture = json.load(f)

    target_layers = [l.strip() for l in args.layers.split(",")]
    q_config = load_questions()

    profile = os.environ.get("AWS_PROFILE", "member1-acc")
    session = boto3.Session(profile_name=profile, region_name="us-east-1")
    from botocore.config import Config as BotoConfig
    bedrock = session.client(
        "bedrock-runtime",
        config=BotoConfig(read_timeout=300, connect_timeout=10),
    )

    results_by_layer = {}
    prev_context = None

    for layer in target_layers:
        layer_fixture = fixture.get("layers", {}).get(layer, {})
        interviews = layer_fixture.get("interview", [])
        if not interviews:
            print(f"WARNING: {layer} fixture에 인터뷰 데이터 없음, skip")
            continue

        print(f"\n[{elapsed()}] === {layer} 리플레이 (fixture: {len(interviews)} Q&A) ===")

        replay_client = ReplayChatClient(interviews)
        agent_cfg = q_config["agents"].get(layer, {})

        system_prompt = agent_cfg.get("system_prompt", "")
        if args.prompt_override:
            with open(args.prompt_override, encoding="utf-8") as pf:
                overrides = json.load(pf)
            if layer in overrides:
                system_prompt = overrides[layer]
                print(f"  [OVERRIDE] {layer} 프롬프트 교체됨 ({len(system_prompt)} chars)")

        # Use first interview answer as devops_answer for fixed-question flow
        devops_answer = interviews[0].get("answer", "") if interviews else None
        if devops_answer:
            print(f"  [REPLAY] Q1 답변 주입: {len(devops_answer)} chars")

        def on_event(event):
            t = event.get("type", "")
            if t == "agent_evaluation":
                print(f"  [EVAL] score={event.get('score')}, verdict={event.get('verdict')}")
            elif t == "agent_thinking":
                print(f"  [THINK] {event.get('thought','')[:100]}...")

        agent = ArchitectAgent(
            agent_type=layer, bedrock_client=bedrock,
            chat_client=replay_client, execution_id="replay-session",
            on_event=on_event, model_id=args.model or agent_cfg.get("model_id", "us.anthropic.claude-sonnet-4-6"),
            max_turns=args.max_turns,
            quality_threshold=agent_cfg.get("quality_threshold", 75),
            system_prompt=system_prompt,
        )

        layer_result = agent.run(context=prev_context, devops_answer=devops_answer)
        results_by_layer[layer] = layer_result or {}
        prev_context = layer_result

        stats = replay_client.stats()
        print(f"[{elapsed()}] {layer} 완료: {stats}")

    # 스코어링
    if "L1" in target_layers and results_by_layer.get("L1"):
        expected = _load_expected(args.expected)
        services = results_by_layer["L1"].get("services", [])
        score = score_grouping(services, expected)
        print_score(score)
        _save_result(target_layers, results_by_layer, score, "replay", args)


def run_compare(args):
    """두 결과 파일 비교."""
    with open(args.compare[0], encoding="utf-8") as f:
        r1 = json.load(f)
    with open(args.compare[1], encoding="utf-8") as f:
        r2 = json.load(f)
    r1["label"] = os.path.basename(args.compare[0])
    r2["label"] = os.path.basename(args.compare[1])
    print_comparison(r1, r2)


# ══════════════════════════════════════════════════════════════════
# 유틸리티
# ══════════════════════════════════════════════════════════════════

def _load_expected(path: str = None) -> dict:
    if not path:
        path = os.path.join(FIXTURES_DIR, "expected_dockercoins.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_result(layers: list, results: dict, score: dict, mode: str, args):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    layers_tag = "_".join(layers)
    path = os.path.join(RESULTS_DIR, f"{ts}_{layers_tag}_{mode}.json")
    data = {
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        "layers": layers,
        "model": getattr(args, "model", None),
        "prompt_override": getattr(args, "prompt_override", None),
        "score": score,
        "layer_results": results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[RESULT] 저장됨: {path}")


# ══════════════════════════════════════════════════════════════════
# 탑다운 모드 — Bedrock 없이 Q1 → Q2 직접 실행
# ══════════════════════════════════════════════════════════════════

def run_topdown(args):
    """탑다운 모드 — DevOps Agent에게 Q1(앱) → Q2(서비스) 직접 질문, Bedrock 없음."""
    import boto3
    from arch_analysis import (
        AgentChatClient, ArchitectureAgentDiscoverer, load_questions,
    )

    profile = os.environ.get("AWS_PROFILE", "member1-acc")
    session = boto3.Session(profile_name=profile, region_name="us-east-1")

    sts = session.client("sts")
    identity = sts.get_caller_identity()
    print(f"AWS Account: {identity['Account']}")

    tagged = {}
    try:
        from overview_app import _get_aws_associations, _session_for_association, _fetch_tagged_resources
        aws_assocs = _get_aws_associations(SPACE_ID)
        for assoc in aws_assocs:
            acct = assoc["account_id"]
            sess = _session_for_association(assoc)
            total, by_service = _fetch_tagged_resources("App", session=sess)
            tagged[acct] = {"total": total, "by_service": by_service, "ok": True}
    except Exception as e:
        print(f"tagged resources 수집 실패 (계속 진행): {e}")

    def on_event(event):
        t = event.get("type", "")
        if t == "phase_start":
            print(f"\n[{elapsed()}] === {event.get('phase')} — {event.get('description')} ===")
        elif t == "agent_question":
            q = event.get("question", "")
            app = event.get("app_name", "")
            prefix = f" ({app})" if app else ""
            print(f"  [Q{prefix}] {q[:200]}...")
        elif t == "agent_answer":
            app = event.get("app_name", "")
            prefix = f" ({app})" if app else ""
            print(f"  [A{prefix}] {len(event.get('answer',''))} chars")
        elif t == "layer_complete":
            layer = event.get("layer", "")
            analysis = event.get("analysis", {})
            nodes = len(analysis.get("graph", {}).get("nodes", []))
            edges = len(analysis.get("graph", {}).get("edges", []))
            print(f"  [{layer} 완료] {nodes} 노드, {edges} 엣지")
        elif t == "layer_progress":
            print(f"  [진행] {event.get('app_name')}: {event.get('nodes_count')} 노드, {event.get('edges_count')} 엣지")
        elif t == "error":
            print(f"  [ERROR] {event.get('error', '')}")

    discoverer = ArchitectureAgentDiscoverer(
        space_id=SPACE_ID, session=session,
        on_event=on_event, tagged_resources=tagged,
    )

    print(f"[{elapsed()}] 탑다운 분석 시작 (Bedrock 없음)")
    result = discoverer.discover()

    # fixture 저장
    if args.save_fixture:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        fixture_path = os.path.join(FIXTURES_DIR, f"arch_topdown_{ts}.json")
        fixture_data = {
            "meta": {
                "captured_at": datetime.now().isoformat(),
                "space_id": SPACE_ID,
                "mode": "topdown",
            },
            "result": result.to_dict(),
            "conversations": result.conversations,
        }
        with open(fixture_path, "w", encoding="utf-8") as f:
            json.dump(fixture_data, f, indent=2, ensure_ascii=False)
        print(f"\n[FIXTURE] 저장됨: {fixture_path}")

    # 결과 요약
    d = result.to_dict()
    nodes = d.get("graph", {}).get("nodes", [])
    edges = d.get("graph", {}).get("edges", [])
    wfs = d.get("workflows", [])
    print(f"\n{'='*60}")
    print(f"  탑다운 분석 결과")
    print(f"{'='*60}")
    print(f"  시스템: {d.get('system_name', '?')}")
    print(f"  노드: {len(nodes)}, 엣지: {len(edges)}, 워크플로우: {len(wfs)}")
    groups = {}
    for n in nodes:
        g = n.get("group", "?")
        groups.setdefault(g, []).append(n.get("name", "?"))
    print(f"  그룹: {len(groups)}개")
    for g, members in sorted(groups.items()):
        print(f"    [{g}]: {', '.join(sorted(members))}")

    # 양방향 edge 체크
    edge_set = set()
    bidir = []
    for e in edges:
        key = (e.get("source"), e.get("target"))
        rev = (e.get("target"), e.get("source"))
        if rev in edge_set:
            bidir.append(f"{key[0]} ↔ {key[1]}")
        edge_set.add(key)
    if bidir:
        print(f"\n  ⚠ 양방향 edge: {bidir}")
    else:
        print(f"\n  ✓ 양방향 edge 없음")

    # 스코어링 (expected가 있으면)
    try:
        expected = _load_expected(args.expected)
        services_for_score = [
            {"name": n.get("name"), "group": n.get("group")}
            for n in nodes
        ]
        score = score_grouping(services_for_score, expected)
        print_score(score)
    except Exception:
        pass

    print()


# ══════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="에이전트 프롬프트 단위 테스트 하네스")
    parser.add_argument("--mode", choices=["live", "replay", "compare", "topdown"], required=True)
    parser.add_argument("--layers", default="L1", help="테스트할 레이어 (콤마 구분, 기본: L1)")
    parser.add_argument("--fixture", help="리플레이용 fixture 파일 경로")
    parser.add_argument("--save-fixture", action="store_true", help="라이브 Q&A를 fixture로 저장")
    parser.add_argument("--prompt-override", help="프롬프트 오버라이드 JSON 파일")
    parser.add_argument("--expected", help="기대 결과 JSON (기본: fixtures/expected_dockercoins.json)")
    parser.add_argument("--model", help="Bedrock 모델 ID 오버라이드")
    parser.add_argument("--max-turns", type=int, default=10, help="레이어당 최대 턴 수")
    parser.add_argument("--compare", nargs=2, help="두 결과 파일 비교")
    args = parser.parse_args()

    if args.mode == "live":
        run_live(args)
    elif args.mode == "replay":
        run_replay(args)
    elif args.mode == "compare":
        run_compare(args)
    elif args.mode == "topdown":
        run_topdown(args)


if __name__ == "__main__":
    main()
