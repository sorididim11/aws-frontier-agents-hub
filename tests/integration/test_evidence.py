#!/usr/bin/env python3
"""
Evidence Dashboard 단위 + 통합 테스트

단위 테스트: 픽스처 데이터 기반, 외부 의존성 없음
통합 테스트: /api/investigation-journal-raw API 호출 필요 (pytest -m integration)
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from evidence import parse_raw_records, build_evidence, make_deep_link


# ── Fixtures ──

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def sample_raw_data():
    """실제 API 데이터 기반 샘플 픽스처 로드."""
    with open(os.path.join(FIXTURES_DIR, "sample_journal_raw.json")) as f:
        data = json.load(f)
    return data["records"]


@pytest.fixture
def parsed_data(sample_raw_data):
    """파싱된 데이터."""
    return parse_raw_records(sample_raw_data)


@pytest.fixture
def evidence_data(parsed_data):
    """빌드된 evidence."""
    return build_evidence(parsed_data)


# ══════════════════════════════════════════════════════════
# 단위 테스트: parse_raw_records
# ══════════════════════════════════════════════════════════


class TestParseRawRecords:

    def test_classifies_record_types(self, sample_raw_data):
        """raw API 응답에서 observation/finding/symptom/summary를 올바르게 분류한다."""
        result = parse_raw_records(sample_raw_data)

        assert len(result["observations"]) == 3
        assert len(result["findings"]) == 2
        assert result["symptom"] is not None
        assert result["summary"] is not None

    def test_observation_keyed_by_id(self, sample_raw_data):
        """observation은 id를 key로 하는 dict이다."""
        result = parse_raw_records(sample_raw_data)
        obs = result["observations"]

        assert "hasher-error_pattern_analysis-2026_04_06_01_15" in obs
        assert "hasher-validation_error_400-2026_04_06_01_10" in obs
        assert "k8s_deployment-rng_env_var_change_corruption_rate-2026_04_06_01_15" in obs

    def test_preserves_signals(self, sample_raw_data):
        """각 observation에 signals 배열이 보존되어야 한다."""
        result = parse_raw_records(sample_raw_data)
        obs = result["observations"]["hasher-error_pattern_analysis-2026_04_06_01_15"]

        assert len(obs["signals"]) >= 1
        assert obs["signals"][0]["type"] == "metric"
        assert obs["signals"][0]["id"] == "error_sum_incident_window"

    def test_preserves_finding_fields(self, sample_raw_data):
        """finding에 id, title, supporting_observations, finding_type이 보존된다."""
        result = parse_raw_records(sample_raw_data)
        f = result["findings"][0]

        assert "id" in f
        assert "title" in f
        assert "supporting_observations" in f
        assert isinstance(f["supporting_observations"], list)

    def test_handles_malformed_content(self):
        """파싱 불가능한 content는 건너뛰고 정상 레코드만 처리한다."""
        records = [
            {"recordType": "observation", "content": "not valid json"},
            {"recordType": "finding", "content": '{"id":"f1","title":"test","supporting_observations":[]}'},
            {"recordType": "message", "content": '{"role":"assistant","content":[{"text":"hello"}]}'},
        ]
        result = parse_raw_records(records)

        assert len(result["observations"]) == 0   # malformed → 건너뜀
        assert len(result["findings"]) == 1        # 정상 처리

    def test_ignores_message_records(self, sample_raw_data):
        """message 타입 레코드는 무시한다."""
        result = parse_raw_records(sample_raw_data)

        # message는 observations/findings/symptom/summary 어디에도 안 들어감
        all_ids = list(result["observations"].keys())
        assert not any("assistant" in oid for oid in all_ids)

    def test_handles_content_as_dict(self):
        """content가 dict인 경우에도 정상 처리한다."""
        records = [
            {"recordType": "finding", "content": {"id": "f2", "title": "dict content", "supporting_observations": ["obs1"]}},
        ]
        result = parse_raw_records(records)

        assert len(result["findings"]) == 1
        assert result["findings"][0]["id"] == "f2"


# ══════════════════════════════════════════════════════════
# 단위 테스트: build_evidence
# ══════════════════════════════════════════════════════════


class TestBuildEvidence:

    def test_finding_observation_link(self, parsed_data):
        """Finding의 supporting_observations로 Observation에 finding_refs가 채워진다."""
        evidence = build_evidence(parsed_data)

        rng_finding = next(f for f in evidence["findings"] if f["id"] == "rng-corruption-active")
        linked_obs_ids = rng_finding["supporting_observations"]

        for obs_id in linked_obs_ids:
            obs = evidence["observations"][obs_id]
            assert any(ref["id"] == "rng-corruption-active" for ref in obs["finding_refs"]), \
                f"observation {obs_id}에 finding_refs가 없음"

    def test_signal_dedup(self, parsed_data):
        """동일 signal ID가 여러 observation에 있어도 한 번만 수집된다."""
        evidence = build_evidence(parsed_data)

        signal_ids = [s["id"] for s in evidence["signals"]]
        assert len(signal_ids) == len(set(signal_ids)), \
            f"중복 signal: {[x for x in signal_ids if signal_ids.count(x) > 1]}"

    def test_stats_counts(self, parsed_data):
        """stats에 signal 유형별 건수가 올바르게 집계된다."""
        evidence = build_evidence(parsed_data)
        stats = evidence["stats"]

        assert "metric" in stats
        total = sum(stats.values())
        assert total == len(evidence["signals"])

    def test_signal_metadata_added(self, parsed_data):
        """각 signal에 _observation_id, _finding_refs, _deep_link이 추가된다."""
        evidence = build_evidence(parsed_data)

        for sig in evidence["signals"]:
            assert "_observation_id" in sig, f"signal {sig['id']}에 _observation_id 없음"
            assert "_finding_refs" in sig, f"signal {sig['id']}에 _finding_refs 없음"
            assert "_deep_link" in sig, f"signal {sig['id']}에 _deep_link 없음"

    def test_all_signal_types_present(self, parsed_data):
        """픽스처 데이터에 포함된 모든 signal 유형이 수집된다."""
        evidence = build_evidence(parsed_data)
        types = set(s["type"] for s in evidence["signals"])

        assert "metric" in types
        assert "trace" in types
        assert "log" in types
        assert "change_event" in types
        assert "code_snippet" in types

    def test_observations_have_finding_refs_list(self, parsed_data):
        """모든 observation에 finding_refs 리스트가 존재한다 (비어있을 수 있음)."""
        evidence = build_evidence(parsed_data)

        for obs_id, obs in evidence["observations"].items():
            assert "finding_refs" in obs, f"observation {obs_id}에 finding_refs 없음"
            assert isinstance(obs["finding_refs"], list)

    def test_finding_with_no_matching_observation(self):
        """존재하지 않는 observation ID를 참조하는 Finding도 정상 처리된다."""
        parsed = {
            "observations": {},
            "findings": [{"id": "f1", "title": "test", "supporting_observations": ["nonexistent_obs"]}],
            "symptom": None,
            "summary": None,
        }
        evidence = build_evidence(parsed)

        assert len(evidence["findings"]) == 1
        assert len(evidence["signals"]) == 0


# ══════════════════════════════════════════════════════════
# 단위 테스트: make_deep_link
# ══════════════════════════════════════════════════════════


class TestMakeDeepLink:

    def test_trace_xray_url(self):
        """trace signal → X-Ray 콘솔 URL을 생성한다."""
        signal = {
            "type": "trace",
            "traces": {"records": [{"trace_id": "1-69d30866-68f47dad3b00d41ee103829a"}]}
        }
        link = make_deep_link(signal, "us-east-1")

        assert link is not None
        assert "xray" in link
        assert "1-69d30866-68f47dad3b00d41ee103829a" in link
        assert "us-east-1" in link

    def test_metric_cloudwatch_url(self):
        """metric signal → CloudWatch Metrics URL을 생성한다."""
        signal = {
            "type": "metric",
            "datasets": {"metricDataset": [{"label": "ApplicationSignals/Error Sum"}]}
        }
        link = make_deep_link(signal, "us-east-1")

        assert link is not None
        assert "cloudwatch" in link
        assert "us-east-1" in link

    def test_code_snippet_github_url(self):
        """code_snippet signal → GitHub URL을 생성한다."""
        signal = {
            "type": "code_snippet",
            "code_snippet": {
                "metadata": {"repository_id": "sorididim11/frontier-devops-agent-test-app"},
                "code_diffs": [{
                    "file_path": {"new": "services/dockercoins/hasher/hasher.rb"},
                    "start_line": {"new": 53}
                }]
            }
        }
        link = make_deep_link(signal, "us-east-1")

        assert link is not None
        assert "github.com/sorididim11" in link
        assert "hasher.rb" in link
        assert "#L53" in link

    def test_log_cloudwatch_logs_url(self):
        """log signal → CloudWatch Logs URL을 생성한다."""
        signal = {"type": "log", "logs": {"source": "cloudwatch"}}
        link = make_deep_link(signal, "us-east-1")

        assert link is not None
        assert "logsV2" in link

    def test_change_event_kubectl_command(self):
        """change_event signal → kubectl 명령어를 생성한다."""
        signal = {
            "type": "change_event",
            "change_event": {"resource": "deployment/rng (dockercoins namespace)"}
        }
        link = make_deep_link(signal, "us-east-1")

        assert link is not None
        assert "kubectl describe deployment rng" in link

    def test_change_event_pod_resource(self):
        """change_event signal (pod) → kubectl describe pod 명령어."""
        signal = {
            "type": "change_event",
            "change_event": {"resource": "pod/rng-f49bb56d4-f4fq8"}
        }
        link = make_deep_link(signal, "us-east-1")

        assert link is not None
        assert "kubectl describe pod rng-f49bb56d4-f4fq8" in link

    def test_missing_trace_records_returns_none(self):
        """trace records가 비어있으면 None을 반환한다."""
        signal = {"type": "trace", "traces": {"records": []}}
        assert make_deep_link(signal, "us-east-1") is None

    def test_unknown_type_returns_none(self):
        """알 수 없는 signal type이면 None을 반환한다."""
        signal = {"type": "unknown_type"}
        assert make_deep_link(signal, "us-east-1") is None

    def test_region_parameter_used(self):
        """region 파라미터가 URL에 반영된다."""
        signal = {
            "type": "trace",
            "traces": {"records": [{"trace_id": "1-abc-def"}]}
        }
        link = make_deep_link(signal, "ap-northeast-2")

        assert "ap-northeast-2" in link


# ══════════════════════════════════════════════════════════
# 통합 테스트: raw API 연동 (pytest -m integration)
# ══════════════════════════════════════════════════════════


RAW_API_URL = os.environ.get(
    "RAW_API_URL",
    "http://localhost:8081/api/investigation-journal-raw"
)
RAW_API_TASK_ID = os.environ.get(
    "RAW_API_TASK_ID",
    "d1f656a7-f577-49a5-990f-64f1ca8ce939"
)


@pytest.mark.integration
class TestRawApiIntegration:

    @pytest.fixture(autouse=True)
    def _fetch_raw(self):
        """raw API 호출하여 데이터 로드."""
        try:
            import requests
        except ImportError:
            pytest.skip("requests 모듈 미설치")
        try:
            resp = requests.get(RAW_API_URL, params={"task_id": RAW_API_TASK_ID}, timeout=10)
            resp.raise_for_status()
            self.raw_data = resp.json()
        except Exception as e:
            pytest.skip(f"raw API 접근 불가: {e}")

    def _get_records(self):
        """응답에서 records 추출 (필드명 유연하게 처리)."""
        return (
            self.raw_data.get("records")
            or self.raw_data.get("raw_records")
            or self.raw_data.get("data", {}).get("records")
            or []
        )

    def test_response_structure(self):
        """raw API가 evidence 처리에 필요한 필드를 반환한다."""
        records = self._get_records()
        assert len(records) > 0, "records가 비어있음"

        types_found = set()
        for r in records:
            rt = r.get("recordType", r.get("record_type", ""))
            assert rt, f"recordType 필드 없음: {list(r.keys())}"
            types_found.add(rt)
            assert "content" in r, f"content 필드 없음: {list(r.keys())}"

        assert "observation" in types_found, f"observation 레코드 없음. 발견된 타입: {types_found}"
        assert "finding" in types_found, f"finding 레코드 없음. 발견된 타입: {types_found}"

    def test_preserves_signals(self):
        """raw API의 observation content에 signals 배열이 보존되어 있다."""
        records = self._get_records()

        found_signals = False
        for r in records:
            rt = r.get("recordType", r.get("record_type", ""))
            if rt == "observation":
                content = r.get("content", "")
                parsed = json.loads(content) if isinstance(content, str) else content
                if isinstance(parsed, dict) and "signals" in parsed and len(parsed["signals"]) > 0:
                    found_signals = True
                    sig = parsed["signals"][0]
                    assert "type" in sig, f"signal에 type 필드 없음: {list(sig.keys())}"
                    assert "id" in sig, f"signal에 id 필드 없음: {list(sig.keys())}"
                    break

        assert found_signals, "observation에 signals 배열이 없음 — raw API가 데이터를 truncate하고 있을 수 있음"

    def test_end_to_end_pipeline(self):
        """raw API → parse → build → 전체 evidence 체인이 동작한다."""
        records = self._get_records()

        parsed = parse_raw_records(records)
        evidence = build_evidence(parsed)

        assert len(evidence["findings"]) >= 1, "findings가 비어있음"
        assert len(evidence["signals"]) >= 1, "signals가 비어있음"

        # 최소 1개 Finding이 Observation과 연결
        linked = [f for f in evidence["findings"] if f["supporting_observations"]]
        assert len(linked) >= 1, "Finding-Observation 연결이 없음"

        # 최소 1개 signal에 deep_link 존재
        with_links = [s for s in evidence["signals"] if s.get("_deep_link")]
        assert len(with_links) >= 1, "deep_link가 있는 signal이 없음"

        # stats 합계 = signals 수
        assert sum(evidence["stats"].values()) == len(evidence["signals"])
