"""ChatWorker — single daemon thread + Queue for DevOps Agent send_message calls.

Replaces the subprocess-based _agent_call.py approach. All Agent API calls go
through one worker thread to avoid connection pool conflicts and enable
centralized localhost URL sanitization + 403 retry.

Usage:
    from chat_worker import init_worker, get_worker
    init_worker(profile="member1-acc", region="us-east-1")
    resp = get_worker().send_raw(space_id, session_id, prompt)
"""
import json
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(__file__))
from arch_analysis import ChatResponse, ChatBlock


_RE_LOCALHOST = re.compile(r'https?://localhost(:\d+)?')
_RE_127 = re.compile(r'https?://127\.0\.0\.1(:\d+)?')


@dataclass
class ChatRequest:
    space_id: str
    session_id: str
    prompt: str
    user_id: str = "scenario"
    result: ChatResponse = None
    error: Exception = None
    done: threading.Event = field(default_factory=threading.Event)


class ChatWorker:
    """Single daemon thread that processes Agent send_message calls sequentially."""

    def __init__(self, profile: str = "member1-acc", region: str = "us-east-1"):
        self._queue: queue.Queue = queue.Queue()
        self._client = None
        self._profile = profile
        self._region = region
        self._thread = threading.Thread(target=self._loop, daemon=True, name="chat-worker")
        self._thread.start()

    def send(self, space_id: str, session_id: str, prompt: str,
             user_id: str = "scenario") -> ChatResponse:
        req = ChatRequest(space_id=space_id, session_id=session_id,
                          prompt=prompt, user_id=user_id)
        self._queue.put(req)
        req.done.wait(timeout=600)
        if not req.done.is_set():
            raise TimeoutError("ChatWorker: 600s timeout waiting for Agent response")
        if req.error:
            raise req.error
        return req.result

    def send_raw(self, space_id: str, session_id: str, prompt: str,
                 user_id: str = "scenario") -> dict:
        resp = self.send(space_id, session_id, prompt, user_id)
        return {"ok": True, "reply": resp.raw_text, "session_id": resp.session_id}

    def _loop(self):
        self._client = self._init_client()
        print(f"[CHAT-WORKER] started (profile={self._profile}, region={self._region})")
        while True:
            req = self._queue.get()
            try:
                prompt = self._sanitize_localhost(req.prompt)
                session_id = req.session_id or self._create_session(req.space_id, req.user_id)
                result = self._send_with_retry(req.space_id, session_id, prompt, req.user_id)
                result.session_id = session_id
                req.result = result
            except Exception as e:
                req.error = e
                print(f"[CHAT-WORKER] error: {type(e).__name__}: {e}")
            finally:
                req.done.set()

    def _init_client(self):
        import boto3
        from botocore.config import Config
        session = boto3.Session(profile_name=self._profile, region_name=self._region)
        return session.client(
            "devops-agent",
            config=Config(read_timeout=300, connect_timeout=10),
        )

    def _create_session(self, space_id: str, user_id: str) -> str:
        resp = self._client.create_chat(agentSpaceId=space_id, userId=user_id)
        exec_id = resp["executionId"]
        print(f"[CHAT-WORKER] new session: {exec_id[:16]}...")
        return exec_id

    def _send_with_retry(self, space_id: str, session_id: str,
                         prompt: str, user_id: str,
                         max_retries: int = 3) -> ChatResponse:
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                t0 = time.time()
                print(f"[CHAT-WORKER] send_message attempt {attempt}/{max_retries}, "
                      f"{len(prompt)} chars → session {session_id[:16]}...")
                resp = self._client.send_message(
                    agentSpaceId=space_id,
                    executionId=session_id,
                    content=prompt,
                    userId=user_id,
                )
                result = self._parse_response(prompt, resp)
                elapsed = time.time() - t0
                print(f"[CHAT-WORKER] done: {len(result.raw_text)} chars in {elapsed:.1f}s")
                return result
            except Exception as e:
                last_err = e
                err_str = str(e)
                if "403" in err_str and attempt < max_retries:
                    print(f"[CHAT-WORKER] 403 on attempt {attempt}, creating new session...")
                    time.sleep(5)
                    session_id = self._create_session(space_id, user_id)
                    continue
                if "responseFailed" in err_str and attempt < max_retries:
                    print(f"[CHAT-WORKER] responseFailed on attempt {attempt}, creating new session...")
                    time.sleep(3)
                    session_id = self._create_session(space_id, user_id)
                    continue
                raise
        raise last_err

    def _parse_response(self, question: str, resp: dict) -> ChatResponse:
        event_stream = resp.get("events", [])
        blocks_data = {}
        failed_error = None
        try:
            for event in event_stream:
                if not isinstance(event, dict):
                    continue
                for etype, edata in event.items():
                    if etype == "responseFailed":
                        failed_error = edata.get("errorMessage", "Unknown Agent error")
                        print(f"[CHAT-WORKER] responseFailed: {edata.get('errorCode')} — {failed_error}")
                    elif etype == "contentBlockStart":
                        idx = edata.get("index", 0)
                        blocks_data[idx] = ChatBlock(
                            index=idx,
                            block_type=edata.get("type", "unknown"),
                            text="",
                            block_id=edata.get("id", ""),
                        )
                    elif etype == "contentBlockDelta":
                        idx = edata.get("index", 0)
                        delta = edata.get("delta", {})
                        text = delta.get("textDelta", {}).get("text", "")
                        if not text:
                            text = delta.get("jsonDelta", {}).get("partialJson", "")
                        if idx in blocks_data:
                            blocks_data[idx].text += text
        finally:
            if hasattr(event_stream, "close"):
                event_stream.close()

        if failed_error:
            raise RuntimeError(f"Agent responseFailed: {failed_error}")

        blocks = [blocks_data[i] for i in sorted(blocks_data.keys())]
        for b in blocks:
            b.text = self._restore_localhost(b.text)
        chat_resp = ChatResponse(question=question, blocks=blocks)
        chat_resp.raw_text = chat_resp.final_text
        return chat_resp

    @staticmethod
    def _sanitize_localhost(text: str) -> str:
        text = _RE_LOCALHOST.sub(r'http://LOCAL_ENDPOINT\1', text)
        text = _RE_127.sub(r'http://LOCAL_ENDPOINT\1', text)
        return text

    @staticmethod
    def _restore_localhost(text: str) -> str:
        return text.replace("LOCAL_ENDPOINT", "localhost")


_workers: dict = {}  # {profile: ChatWorker}


def init_worker(profile: str = "member1-acc", region: str = "us-east-1") -> ChatWorker:
    if profile not in _workers:
        _workers[profile] = ChatWorker(profile=profile, region=region)
    return _workers[profile]


def get_worker(profile: str = None) -> ChatWorker:
    if profile and profile in _workers:
        return _workers[profile]
    if not profile and len(_workers) == 1:
        return next(iter(_workers.values()))
    if not profile and _workers:
        return next(iter(_workers.values()))
    raise RuntimeError(f"ChatWorker not initialized — call init_worker() first (profile={profile})")
