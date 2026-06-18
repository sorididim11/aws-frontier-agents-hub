"""Architecture analysis worker — runs as independent subprocess.

Invoked by Flask endpoint, survives Flask restarts.
Writes events to DDB for SSE relay by Flask.

Usage:
    python arch_worker.py --space-id <id> --model <key> [--resume] [--app-name <name>]
"""
import argparse
import json
import os
import sys
import time
import threading
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("PYTHONUNBUFFERED", "1")


def _boto_session():
    import boto3
    from app_config import AWS_PROFILE, AWS_REGION
    try:
        return boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    except Exception:
        return boto3.Session(region_name=AWS_REGION)


def _events_table():
    from app_config import RUNS_TABLE
    return _boto_session().resource("dynamodb").Table(RUNS_TABLE)


def _sanitize_ddb(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _sanitize_ddb(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_ddb(i) for i in obj]
    return obj


def _put_event(run_id, seq, event):
    """Write a single event to DDB."""
    item = {
        "run_id": run_id,
        "record_type": f"arch_event#{seq:06d}",
        "seq": seq,
        "event": json.dumps(event, ensure_ascii=False, default=str),
        "ts": Decimal(str(time.time())),
    }
    _events_table().put_item(Item=_sanitize_ddb(item))


def _put_status(run_id, status, error_msg=None):
    """Write worker status."""
    item = {
        "run_id": run_id,
        "record_type": "arch_worker_status",
        "status": status,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    if error_msg:
        item["error_msg"] = error_msg[:500]
    _events_table().put_item(Item=_sanitize_ddb(item))


def _collect_tagged(space_id, app_name=None):
    from app_config import _get_aws_associations, _session_for_association, _fetch_tagged_resources
    tagged = {}
    try:
        aws_assocs = _get_aws_associations(space_id)
        for assoc in aws_assocs:
            acct = assoc["account_id"]
            sess = _session_for_association(assoc)
            total, by_service = _fetch_tagged_resources(
                tag_key="App", tag_value=app_name, session=sess)
            tagged[acct] = {"total": total, "by_service": by_service, "ok": True}
    except Exception as e:
        print(f"[WORKER] tagged resources 수집 실패: {e}")
    return tagged


def run(space_id, model_key, resume=False, target_app=""):
    from app_config import AVAILABLE_MODELS, AGENT_SPACE_ID
    from ai_provider import init_provider
    from arch_analysis import ArchitectureAgentDiscoverer

    init_provider()

    model_id = AVAILABLE_MODELS.get(model_key, AVAILABLE_MODELS["sonnet"])
    effective_space = space_id or AGENT_SPACE_ID

    # Run ID for this worker session
    run_id = f"arch-worker-{effective_space}"

    seq_counter = [0]
    cancel_event = threading.Event()

    def on_event(event):
        seq_counter[0] += 1
        seq = seq_counter[0]
        try:
            _put_event(run_id, seq, event)
        except Exception as e:
            print(f"[WORKER] DDB event write failed (seq={seq}): {e}")
        # Also print for debugging
        etype = event.get("type", "?")
        print(f"[WORKER] event #{seq}: {etype}")

    # Update status
    _put_status(run_id, "running")
    print(f"[WORKER] 시작: space={effective_space}, model={model_key}, resume={resume}")

    # Load checkpoint if resuming
    checkpoint = None
    if resume:
        try:
            from routes_arch import _load_arch_checkpoint
            checkpoint = _load_arch_checkpoint(effective_space)
            if checkpoint:
                print(f"[WORKER] 체크포인트 복원: {checkpoint.get('completed_layers', [])}")
        except Exception as e:
            print(f"[WORKER] 체크포인트 로드 실패: {e}")

    try:
        session = _boto_session()

        if not target_app:
            from app_config import resolve_app_name_and_tag
            target_app, target_tag = resolve_app_name_and_tag(effective_space)
            if target_app:
                print(f"[WORKER] 앱 이름: {target_app}, 태그: {target_tag}")
        else:
            target_tag = target_app

        disc = ArchitectureAgentDiscoverer(
            space_id=effective_space,
            session=session,
            on_event=on_event,
            model_id=model_id,
            tagged_resources=None,
            app_name=target_app or None,
            app_tag_value=target_tag or None,
        )

        analysis = disc.discover(checkpoint=checkpoint, cancel_event=cancel_event)

        # Save final analysis
        from routes_arch import _save_arch_analysis, _delete_arch_checkpoint
        run_id_saved = _save_arch_analysis(effective_space, analysis, model_id)
        try:
            _delete_arch_checkpoint(effective_space)
        except Exception:
            pass
        print(f"[WORKER] 분석 저장 완료: {run_id_saved}")

        # Emit complete event
        on_event({"type": "complete", "analysis": analysis.to_dict()})
        _put_status(run_id, "complete")

    except Exception as e:
        import traceback
        print(f"[WORKER] 에러: {e}")
        traceback.print_exc()
        on_event({"type": "error", "error": str(e)})
        _put_status(run_id, "error", str(e))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Architecture analysis worker")
    parser.add_argument("--space-id", required=True)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--app-name", default="")
    parser.add_argument("--enrich-k8s", action="store_true")
    parser.add_argument("--enrich-network", action="store_true")
    parser.add_argument("--enrich-security", action="store_true")
    args = parser.parse_args()

    run(args.space_id, args.model, args.resume, args.app_name)
