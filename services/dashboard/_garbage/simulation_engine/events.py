"""Simulation Engine v2 — SSE event relay.

Orchestrator의 이벤트를 SSE 스트림으로 변환하여 UI에 전달.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import asdict

from simulation_engine.contracts import SimulationEvent


class SSERelay:
    """Thread-safe event queue → SSE stream 변환.

    Usage:
        relay = SSERelay()
        # Orchestrator에 on_event=relay.push 전달
        # Flask route에서 relay.stream() 반환
    """

    def __init__(self, max_size: int = 200):
        self._queue: queue.Queue[SimulationEvent | None] = queue.Queue(maxsize=max_size)
        self._closed = False

    def push(self, event: SimulationEvent):
        """Orchestrator가 호출 — 이벤트를 큐에 적재."""
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(event)
            except queue.Empty:
                pass

    def close(self):
        """스트림 종료 신호."""
        self._closed = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(None)
            except queue.Empty:
                pass

    def stream(self):
        """Flask Response generator — SSE 포맷으로 yield.

        Usage in Flask route:
            return Response(relay.stream(), mimetype='text/event-stream')
        """
        while True:
            try:
                event = self._queue.get(timeout=30)
            except queue.Empty:
                yield f": keepalive\n\n"
                continue

            if event is None:
                yield f"event: close\ndata: {{}}\n\n"
                break

            data = json.dumps(event.data, ensure_ascii=False)
            yield f"event: {event.event_type}\ndata: {data}\n\n"


# ──────────────────────────────────────────────
# Active runs registry (in-memory)
# ──────────────────────────────────────────────

_runs_lock = threading.Lock()
_active_relays: dict[str, SSERelay] = {}


def create_relay(run_id: str) -> SSERelay:
    """새 run에 대한 SSE relay 생성 및 등록."""
    relay = SSERelay()
    with _runs_lock:
        _active_relays[run_id] = relay
    return relay


def get_relay(run_id: str) -> SSERelay | None:
    """run_id로 relay 조회."""
    with _runs_lock:
        return _active_relays.get(run_id)


def remove_relay(run_id: str):
    """완료된 run의 relay 제거."""
    with _runs_lock:
        relay = _active_relays.pop(run_id, None)
    if relay:
        relay.close()
