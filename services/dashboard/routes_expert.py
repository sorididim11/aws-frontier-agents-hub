"""Expert Agent routes — SSE proxy to Node.js sidecar for Claude Code chat."""
import json
import os

import requests
from flask import Blueprint, Response, jsonify, request, stream_with_context

expert_bp = Blueprint("expert_bp", __name__)


def _sidecar_url():
    env = os.environ.get("EXPERT_SIDECAR_URL")
    if env:
        return env
    try:
        from app_config import _CFG, _cfg_get
        port = _cfg_get(_CFG, "server.sidecar_port", "3100")
        return f"http://localhost:{port}"
    except Exception:
        return "http://localhost:3100"


SIDECAR_URL = _sidecar_url()


@expert_bp.route("/api/expert/chat", methods=["POST"])
def api_expert_chat():
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    session_id = data.get("sessionId", "")

    page_context = data.get("pageContext")

    if not prompt:
        return jsonify({"ok": False, "error": "prompt required"}), 400

    provider = data.get("provider")

    def generate():
        try:
            payload = {"prompt": prompt, "sessionId": session_id or None}
            if page_context:
                payload["pageContext"] = page_context
            if provider:
                payload["provider"] = provider
            resp = requests.post(
                f"{SIDECAR_URL}/api/chat",
                json=payload,
                stream=True,
                timeout=(5, 300),
            )
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if line:
                    yield f"data: {line}\n\n"
        except requests.ConnectionError:
            yield f"data: {json.dumps({'type': 'error', 'content': 'Expert sidecar not available. Start it with: cd expert_sidecar && npm run dev'})}\n\n"
        except requests.Timeout:
            yield f"data: {json.dumps({'type': 'error', 'content': 'Request timed out'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@expert_bp.route("/api/expert/health")
def api_expert_health():
    try:
        resp = requests.get(f"{SIDECAR_URL}/health", timeout=2)
        return jsonify(resp.json())
    except Exception:
        return jsonify({"ok": False, "error": "sidecar unreachable"})


@expert_bp.route("/api/agent-chat", methods=["POST"])
def api_agent_chat():
    """MCP tool backend — relay message to Agent Space via ChatWorker."""
    body = request.get_json(silent=True) or {}
    space_id = (body.get("space_id") or "").strip()
    message = (body.get("message") or "").strip()
    session_id = (body.get("session_id") or "").strip()

    if not space_id or not message:
        return jsonify({"ok": False, "error": "space_id and message required"}), 400

    try:
        from chat_worker import init_worker, get_worker
        from app_config import _profile_for_space, AWS_REGION
        profile = _profile_for_space(space_id)
        init_worker(profile=profile, region=AWS_REGION)
        worker = get_worker(profile)
        result = worker.send_raw(space_id=space_id, session_id=session_id, prompt=message)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@expert_bp.route("/api/agent-sessions")
def api_agent_sessions():
    """List active Agent Space chat sessions."""
    space_id = request.args.get("space_id", "").strip()
    if not space_id:
        return jsonify({"ok": False, "error": "space_id required"}), 400

    try:
        from app_config import _boto_session
        session = _boto_session()
        client = session.client("devops-agent")
        resp = client.list_chats(agentSpaceId=space_id, maxResults=20)
        chats = resp.get("chats", [])
        return jsonify({"ok": True, "sessions": chats})
    except Exception as e:
        return jsonify({"ok": False, "sessions": [], "error": str(e)})
