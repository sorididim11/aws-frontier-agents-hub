"""Unit tests for simulation_engine.events (SSERelay)."""

import sys
import os
import json
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "services", "dashboard"))

from simulation_engine.contracts import SimulationEvent
from simulation_engine.events import SSERelay, create_relay, get_relay, remove_relay


def test_relay_push_and_stream():
    relay = SSERelay()
    relay.push(SimulationEvent(event_type="round_start", data={"round": 1}))
    relay.push(SimulationEvent(event_type="verdict", data={"passed": True}))
    relay.close()

    chunks = list(relay.stream())
    assert len(chunks) == 3  # 2 events + close

    assert "event: round_start" in chunks[0]
    assert '"round": 1' in chunks[0]

    assert "event: verdict" in chunks[1]
    assert '"passed": true' in chunks[1]

    assert "event: close" in chunks[2]


def test_relay_keepalive_on_timeout():
    relay = SSERelay()

    received = []

    def consumer():
        for chunk in relay.stream():
            received.append(chunk)
            if "close" in chunk:
                break

    t = threading.Thread(target=consumer, daemon=True)
    t.start()

    time.sleep(0.1)
    relay.push(SimulationEvent(event_type="test", data={"ok": True}))
    time.sleep(0.1)
    relay.close()
    t.join(timeout=5)

    event_chunks = [c for c in received if c.startswith("event:")]
    assert any("event: test" in c for c in event_chunks)
    assert any("event: close" in c for c in received)


def test_relay_overflow_drops_oldest():
    relay = SSERelay(max_size=5)
    for i in range(8):
        relay.push(SimulationEvent(event_type="tick", data={"i": i}))
    relay.close()

    chunks = [c for c in relay.stream() if c.startswith("event: tick")]
    # close()도 큐 슬롯을 사용하므로 최종 tick 수 = max_size - 1
    assert len(chunks) == 4
    # 가장 오래된 이벤트가 drop됨 — 마지막 이벤트들만 남음
    last_data = json.loads(chunks[-1].split("data: ")[1].strip())
    assert last_data["i"] >= 5


def test_relay_close_idempotent():
    relay = SSERelay()
    relay.close()
    relay.close()
    relay.push(SimulationEvent(event_type="after_close", data={}))
    chunks = list(relay.stream())
    assert not any("after_close" in c for c in chunks)


def test_registry_create_get_remove():
    relay = create_relay("test-run-001")
    assert get_relay("test-run-001") is relay

    remove_relay("test-run-001")
    assert get_relay("test-run-001") is None


def test_registry_remove_nonexistent():
    remove_relay("nonexistent-id")


def test_relay_concurrent_push():
    relay = SSERelay(max_size=100)

    def pusher(prefix, count):
        for i in range(count):
            relay.push(SimulationEvent(event_type=f"{prefix}_{i}", data={}))

    threads = [threading.Thread(target=pusher, args=(f"t{n}", 20)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    relay.close()
    chunks = [c for c in relay.stream() if c.startswith("event:")]
    assert len(chunks) <= 100
    assert len(chunks) >= 50
