#!/usr/bin/env python3
"""
아키텍처 인터뷰 흐름 테스트 — 프로덕션 코드(arch_analysis.py)를 직접 호출하여
모든 이벤트와 타이밍을 콘솔에 출력합니다.

사용법:
  AWS_PROFILE=member1-acc python3 test_arch_flow.py [--layers L1] [--layers L1,L2]

옵션:
  --layers L1        L1만 실행 (기본: L1,L2,L3 전체)
  --layers L1,L2     L1+L2만 실행
  --max-turns 5      레이어당 최대 턴 수 (기본: 10)
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

SPACE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

T0 = time.time()


def elapsed():
    return f"{time.time() - T0:6.1f}s"


def on_event(event):
    t = event.get("type", "?")
    agent = event.get("agent", "")
    ts = elapsed()

    if t == "phase_start":
        print(f"\n{'='*60}")
        print(f"[{ts}] ▶ {agent} 시작: {event.get('label', '')}")
        print(f"{'='*60}")

    elif t == "agent_thinking":
        thought = event.get("thought", "")[:200]
        print(f"[{ts}]   💭 {agent} turn {event.get('turn')}: {thought}")

    elif t == "agent_question":
        q = event.get("question", "")
        print(f"[{ts}]   ❓ {agent} turn {event.get('turn')}: {q[:300]}")

    elif t == "agent_answer":
        a = event.get("answer", "")[:200]
        tools = event.get("tool_calls", [])
        print(f"[{ts}]   ✅ {agent} turn {event.get('turn')}: "
              f"답변 {len(event.get('answer',''))}자, tools={len(tools)}")
        if tools:
            for tc in tools[:3]:
                print(f"[{ts}]      tool: {tc[:100]}")

    elif t == "agent_evaluation":
        print(f"[{ts}]   📊 {agent}: score={event.get('score')}, "
              f"verdict={event.get('verdict')}")

    elif t == "phase_complete":
        print(f"[{ts}]   ✓ {agent} 완료")

    elif t == "layer_complete":
        layer = event.get("layer", "?")
        restored = event.get("restored", False)
        a = event.get("analysis", {})
        g = a.get("graph", {})
        nodes = len(g.get("nodes", []))
        edges = len(g.get("edges", []))
        print(f"\n[{ts}] ★ {layer} 완료 {'(복원)' if restored else ''}: "
              f"{nodes} 노드, {edges} 엣지")
        if a.get("compute"):
            print(f"         compute: {len(a['compute'])}")
        if a.get("managed_services"):
            print(f"         managed: {len(a['managed_services'])}")
        if a.get("spof"):
            print(f"         SPOF: {len(a['spof'])}")

    elif t == "error":
        print(f"[{ts}] ❌ ERROR: {event.get('error', '')}")

    else:
        print(f"[{ts}]   [{t}] {json.dumps(event, ensure_ascii=False)[:200]}")


def main():
    parser = argparse.ArgumentParser(description="아키텍처 인터뷰 흐름 테스트")
    parser.add_argument("--layers", default="L1,L2,L3",
                        help="실행할 레이어 (콤마 구분, 기본: L1,L2,L3)")
    parser.add_argument("--max-turns", type=int, default=10,
                        help="레이어당 최대 턴 수 (기본: 10)")
    args = parser.parse_args()

    target_layers = [l.strip() for l in args.layers.split(",")]
    print(f"대상 레이어: {target_layers}, max_turns: {args.max_turns}")

    import boto3
    from arch_analysis import ArchitectureAgentDiscoverer

    profile = os.environ.get("AWS_PROFILE", "member1-acc")
    session = boto3.Session(profile_name=profile, region_name="us-east-1")

    sts = session.client("sts")
    identity = sts.get_caller_identity()
    print(f"AWS Account: {identity['Account']}, ARN: {identity['Arn']}")

    # tagged resources 수집
    from overview_app import _get_aws_associations, _session_for_association, _fetch_tagged_resources
    tagged = {}
    try:
        aws_assocs = _get_aws_associations(SPACE_ID)
        for assoc in aws_assocs:
            acct = assoc["account_id"]
            sess = _session_for_association(assoc)
            total, by_service = _fetch_tagged_resources("App", session=sess)
            tagged[acct] = {"total": total, "by_service": by_service, "ok": True}
            print(f"tagged resources ({acct}): {total}")
    except Exception as e:
        print(f"tagged resources 수집 실패: {e}")

    # discoverer 실행
    prompt_overrides = {}
    for layer in target_layers:
        prompt_overrides[layer] = {"max_turns": args.max_turns}

    # ArchitectureAgentDiscoverer.LAYERS를 target_layers로 제한
    original_layers = ArchitectureAgentDiscoverer.LAYERS
    ArchitectureAgentDiscoverer.LAYERS = target_layers

    disc = ArchitectureAgentDiscoverer(
        space_id=SPACE_ID, session=session,
        on_event=on_event,
        model_id="us.anthropic.claude-sonnet-4-6",
        prompt_overrides=prompt_overrides,
        tagged_resources=tagged,
    )

    print(f"\n[{elapsed()}] discover 시작...")
    try:
        result = disc.discover()
        print(f"\n{'='*60}")
        print(f"[{elapsed()}] 전체 완료")
        print(f"  노드: {len(result.graph.nodes)}")
        print(f"  엣지: {len(result.graph.edges)}")
        print(f"  시스템: {result.system_name}")
        print(f"  설명: {result.description}")
        if result.workflows:
            print(f"  워크플로우: {len(result.workflows)}")
        print(f"{'='*60}")
    except Exception as e:
        print(f"\n[{elapsed()}] ❌ discover 실패: {e}")
        import traceback
        traceback.print_exc()
    finally:
        ArchitectureAgentDiscoverer.LAYERS = original_layers


if __name__ == "__main__":
    main()
