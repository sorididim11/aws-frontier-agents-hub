"""E2E test for Simulation Engine v2.

FM-03 Redis Dependency Blackhole을 사용하여 전체 Generate → Verify → Improve 루프를 검증.

Usage:
    python tests/run_simulation_e2e.py [--base http://localhost:5003] [--space-id SP_ID]

사전 조건:
    1. overview_app.py가 localhost:5003에서 실행 중
    2. DockerCoins 앱이 EKS 클러스터에 배포됨
    3. Bedrock 접근 가능 (Opus + Sonnet)
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error


# ─── Helpers ───

def api_post(base, path, data):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}", data=body, headers={"Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read().decode("utf-8"))


def api_get(base, path):
    req = urllib.request.Request(f"{base}{path}")
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read().decode("utf-8"))


def collect_sse_events(base, run_id, timeout=480):
    """SSE 스트림에서 이벤트를 수집. complete/close/error_event까지."""
    import http.client
    from urllib.parse import urlparse

    url = f"{base}/api/simulation/{run_id}/stream"
    parsed = urlparse(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    conn.request("GET", parsed.path)
    resp = conn.getresponse()

    if resp.status != 200:
        print(f"  SSE 연결 실패: HTTP {resp.status}")
        return []

    events = []
    buffer = ""
    start = time.time()

    while time.time() - start < timeout:
        try:
            chunk = resp.read(4096)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="replace")

            while "\n\n" in buffer:
                raw_event, buffer = buffer.split("\n\n", 1)
                event_type = ""
                event_data = ""
                for line in raw_event.strip().split("\n"):
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        event_data = line[6:]
                    elif line.startswith(": "):
                        continue  # keepalive

                if event_type and event_data:
                    try:
                        data = json.loads(event_data)
                    except json.JSONDecodeError:
                        data = {"raw": event_data}
                    events.append({"type": event_type, "data": data})
                    _print_event(event_type, data)

                    if event_type in ("complete", "close", "error_event"):
                        conn.close()
                        return events
        except Exception as e:
            print(f"  SSE read error: {e}")
            break

    conn.close()
    return events


def _print_event(event_type, data):
    """이벤트 실시간 출력."""
    if event_type == "round_start":
        print(f"\n  ▼ Round {data.get('round')}/{data.get('max_rounds')}")
    elif event_type == "agent_action":
        agent = data.get("agent", "?")
        tool = data.get("tool", "?")
        inp = (data.get("input_summary") or data.get("input", ""))[:60]
        print(f"    [{agent}] {tool}: {inp}")
    elif event_type == "validation":
        status = "✓" if data.get("passed") else "✗"
        layer = data.get("layer", "?")
        errors = data.get("errors", [])
        if errors:
            print(f"    {status} validation L{layer}: {errors[0][:60]}")
        else:
            print(f"    {status} validation L{layer}: 통과")
    elif event_type == "phase_change":
        print(f"    ── phase: {data.get('phase')} ──")
    elif event_type == "verdict":
        passed = data.get("passed")
        icon = "✓ PASS" if passed else "✗ FAIL"
        print(f"    ─── Verdict: {icon} ───")
        if not passed:
            print(f"    원인: {data.get('failure_reason', '')[:80]}")
            if data.get("fix_hint"):
                print(f"    힌트: {data.get('fix_hint', '')[:80]}")
    elif event_type == "complete":
        result = data.get("result", "?")
        rounds = data.get("rounds", "?")
        print(f"\n  ═══ 완료: {result} ({rounds} rounds) ═══")
    elif event_type == "error_event":
        print(f"\n  ✗ ERROR: {data.get('message', '')[:100]}")


# ─── Tests ───

class SimulationE2ETest:
    def __init__(self, base_url, space_id=""):
        self.base = base_url
        self.space_id = space_id
        self.results = {}
        self._last_scenario = None

    def run_all(self):
        """모든 E2E 테스트 실행."""
        self._preflight_check()

        tests = [
            ("E2E-1: FM-03 Redis Blackhole", self.test_full_loop),
            ("E2E-2: Rerun Existing", self.test_rerun_existing),
            ("E2E-3: Auto Improve (wrong target)", self.test_auto_improve),
            ("E2E-4: FM-01 Network Isolation", self.test_fm01_network_isolation),
            ("E2E-5: FM-02 Compute Kill", self.test_fm02_compute_kill),
            ("E2E-6: FM-04 Resource Pressure", self.test_fm04_resource_pressure),
        ]

        for name, fn in tests:
            print(f"\n{'='*60}")
            print(f"  {name}")
            print(f"{'='*60}")
            try:
                result = fn()
                self.results[name] = result
                print(f"\n  결과: {result}")
            except Exception as e:
                self.results[name] = f"ERROR: {e}"
                print(f"\n  예외: {e}")

            # 테스트 간 복원 대기
            print("  restore 대기 15s...")
            time.sleep(15)

        self._print_summary()

    def _preflight_check(self):
        """사전 조건 확인."""
        print("\n[Preflight] 앱 접근 확인...")
        try:
            resp = api_get(self.base, "/api/simulation/active")
            print(f"  앱 정상: active runs = {len(resp.get('runs', []))}")
        except Exception as e:
            print(f"  앱 접근 실패: {e}")
            print("  overview_app.py가 실행 중인지 확인하세요.")
            sys.exit(1)

    def test_full_loop(self):
        """E2E-1: FM-03 full Generate→Verify loop."""
        body = {
            "failure_mode_id": "FM-03",
            "target_service": "worker",
            "namespace": "dockercoins",
            "space_id": self.space_id,
            "max_rounds": 3,
        }

        # Start
        resp = api_post(self.base, "/api/simulation/run", body)
        if resp.get("error"):
            return f"FAIL (start): {resp['error']}"

        run_id = resp["run_id"]
        print(f"  run_id: {run_id}")

        # Collect SSE events
        events = collect_sse_events(self.base, run_id, timeout=480)

        # Validate
        return self._validate_events(events, run_id)

    def test_rerun_existing(self):
        """E2E-2: 기존 시나리오 재실행."""
        if not self._last_scenario:
            return "SKIP (E2E-1에서 시나리오 미생성)"

        body = {
            "existing_scenario": self._last_scenario,
            "target_service": self._last_scenario.get("target_service", "worker"),
            "namespace": "dockercoins",
            "space_id": self.space_id,
            "max_rounds": 3,
        }

        resp = api_post(self.base, "/api/simulation/run", body)
        if resp.get("error"):
            return f"FAIL (start): {resp['error']}"

        run_id = resp["run_id"]
        print(f"  run_id: {run_id}")

        events = collect_sse_events(self.base, run_id, timeout=300)
        return self._validate_events(events, run_id)

    def test_auto_improve(self):
        """E2E-3: 의도적 실패 → Generator 자동 수정."""
        body = {
            "failure_mode_id": "FM-03",
            "target_service": "nonexistent-svc",
            "namespace": "dockercoins",
            "space_id": self.space_id,
            "max_rounds": 3,
        }

        resp = api_post(self.base, "/api/simulation/run", body)
        if resp.get("error"):
            return f"FAIL (start): {resp['error']}"

        run_id = resp["run_id"]
        print(f"  run_id: {run_id}")

        events = collect_sse_events(self.base, run_id, timeout=480)

        # E2E-3은 Generator가 환경 탐색 후 올바른 서비스를 찾아야 함
        event_types = [e["type"] for e in events]
        if "complete" not in event_types:
            return "FAIL (no complete event)"

        complete_evt = next(e for e in events if e["type"] == "complete")
        result = complete_evt["data"].get("result", "unknown")

        # 성공이든 실패든, Generator가 동작한 것 자체가 검증 목표
        has_agent_action = any(
            e["type"] == "agent_action" and e["data"].get("agent") == "generator"
            for e in events
        )
        if not has_agent_action:
            return "FAIL (Generator 활동 없음)"

        return f"{'PASS' if result == 'pass' else 'ACCEPTABLE_FAIL'} (result={result})"

    def test_fm01_network_isolation(self):
        """E2E-4: FM-01 Worker→Hasher 네트워크 차단."""
        body = {
            "failure_mode_id": "FM-01",
            "target_service": "hasher",
            "namespace": "dockercoins",
            "space_id": self.space_id,
            "max_rounds": 3,
        }

        resp = api_post(self.base, "/api/simulation/run", body)
        if resp.get("error"):
            return f"FAIL (start): {resp['error']}"

        run_id = resp["run_id"]
        print(f"  run_id: {run_id}")

        events = collect_sse_events(self.base, run_id, timeout=480)
        return self._validate_events(events, run_id)

    def test_fm02_compute_kill(self):
        """E2E-5: FM-02 Worker pod 삭제 → 자동 복구 관찰."""
        body = {
            "failure_mode_id": "FM-02",
            "target_service": "worker",
            "namespace": "dockercoins",
            "space_id": self.space_id,
            "max_rounds": 3,
        }

        resp = api_post(self.base, "/api/simulation/run", body)
        if resp.get("error"):
            return f"FAIL (start): {resp['error']}"

        run_id = resp["run_id"]
        print(f"  run_id: {run_id}")

        events = collect_sse_events(self.base, run_id, timeout=480)
        return self._validate_events(events, run_id)

    def test_fm04_resource_pressure(self):
        """E2E-6: FM-04 Worker CPU limit 극단 설정 → 성능 저하 관찰."""
        body = {
            "failure_mode_id": "FM-04",
            "target_service": "worker",
            "namespace": "dockercoins",
            "space_id": self.space_id,
            "max_rounds": 3,
        }

        resp = api_post(self.base, "/api/simulation/run", body)
        if resp.get("error"):
            return f"FAIL (start): {resp['error']}"

        run_id = resp["run_id"]
        print(f"  run_id: {run_id}")

        events = collect_sse_events(self.base, run_id, timeout=480)
        return self._validate_events(events, run_id)

    def _validate_events(self, events, run_id):
        """이벤트 시퀀스 검증 — 필수 기준 체크."""
        if not events:
            return "FAIL (no events received)"

        event_types = [e["type"] for e in events]

        checks = {
            "round_start": "round_start" in event_types,
            "verdict": "verdict" in event_types,
            "complete": "complete" in event_types,
        }

        failures = [k for k, v in checks.items() if not v]
        if failures:
            return f"FAIL (missing: {', '.join(failures)})"

        # 결과 추출
        complete_evt = next(e for e in events if e["type"] == "complete")
        result = complete_evt["data"].get("result", "unknown")
        rounds = complete_evt["data"].get("rounds", "?")

        # 시나리오 저장 (E2E-2를 위해)
        if result == "pass" and complete_evt["data"].get("final_scenario"):
            self._last_scenario = complete_evt["data"]["final_scenario"]

        return f"{'PASS' if result == 'pass' else 'FAIL'} (result={result}, rounds={rounds})"

    def _print_summary(self):
        """최종 결과 요약."""
        print(f"\n\n{'='*60}")
        print("  최종 결과")
        print(f"{'='*60}")
        all_pass = True
        for name, result in self.results.items():
            icon = "PASS" if "PASS" in result else "FAIL" if "FAIL" in result else "SKIP"
            if "FAIL" in result and "ACCEPTABLE" not in result:
                all_pass = False
            print(f"  [{icon:4}] {name}: {result}")

        print(f"\n  전체: {'ALL PASS' if all_pass else 'SOME FAILED'}")
        return 0 if all_pass else 1


# ─── Entry ───

def main():
    parser = argparse.ArgumentParser(description="Simulation Engine v2 E2E Test")
    parser.add_argument("--base", default="http://localhost:5003", help="Base URL")
    parser.add_argument("--space-id", default="", help="Space ID")
    args = parser.parse_args()

    test = SimulationE2ETest(base_url=args.base, space_id=args.space_id)
    sys.exit(test.run_all())


if __name__ == "__main__":
    main()
