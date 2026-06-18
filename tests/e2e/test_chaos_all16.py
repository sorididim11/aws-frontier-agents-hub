#!/usr/bin/env python3
"""
16 Failure Mode 전체 자동 검증 프레임워크
========================================
Phase 1: 시나리오 생성 (POST /api/scenario-chat)
Phase 2: 시나리오 실행 (POST /api/scenario-run/{id})
Phase 3: 에러 분류 + 근본 원인 분석 + 리포트

사용법:
  python3 test_chaos_all16.py                       # 전체 (생성+실행)
  python3 test_chaos_all16.py --phase generate      # 생성만
  python3 test_chaos_all16.py --phase execute       # 이전 생성 결과로 실행만
  python3 test_chaos_all16.py --modes FM-04,FM-08   # 특정 FM만
  python3 test_chaos_all16.py --parallel 2          # 병렬도 (기본 1)
"""

import argparse
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_CONFIG = {
    "base_url": os.environ.get("TEST_BASE_URL", "http://localhost:5003"),
}
SPACE_ID = os.environ.get("TEST_SPACE_ID", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
OUTPUT_DIR = Path(__file__).parent / "_test_scenarios"
REPORT_PATH = OUTPUT_DIR / "report_all16.json"


def set_base_url(url: str):
    _CONFIG["base_url"] = url

GENERATION_TIMEOUT = 600  # seconds per scenario generation
SCRIPT_GEN_TIMEOUT = 600
EXECUTION_TIMEOUT = 720   # 12 min max per scenario execution
POLL_INTERVAL = 15
COOLDOWN = 30

ALL_FM_IDS = [
    "FM-01", "FM-02", "FM-03", "FM-04", "FM-05",
    "FM-06", "FM-07", "FM-08", "FM-09", "FM-10",
    "FM-12", "FM-15", "FM-18", "FM-19", "FM-21", "FM-22",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class GenerationResult:
    fm_id: str
    fm_name: str = ""
    success: bool = False
    scenario_id: str = ""
    scenario: dict = field(default_factory=dict)
    script_generated: bool = False
    script_dry_run: str = ""
    error: str = ""
    error_category: str = ""  # scenario | code | infra
    error_pattern: str = ""   # root cause pattern
    duration: float = 0.0
    fixes_applied: list = field(default_factory=list)
    validation_errors: list = field(default_factory=list)


@dataclass
class ExecutionResult:
    fm_id: str
    scenario_id: str = ""
    success: bool = False
    run_id: str = ""
    status: str = ""
    result: str = ""
    steps_detail: list = field(default_factory=list)
    error: str = ""
    error_category: str = ""
    error_pattern: str = ""
    duration: float = 0.0


@dataclass
class RootCause:
    pattern: str
    category: str
    affected_fms: list = field(default_factory=list)
    description: str = ""
    fix_suggestion: str = ""


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _base():
    return _CONFIG["base_url"]


def _post(path, json_body=None, timeout=120):
    resp = requests.post(f"{_base()}{path}", json=json_body, timeout=timeout)
    return resp.status_code, resp.json()


def _get(path, params=None, timeout=30):
    resp = requests.get(f"{_base()}{path}", params=params, timeout=timeout)
    return resp.status_code, resp.json()


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

def classify_generation_error(status_code, response, error_str=""):
    """HTTP 응답과 에러 메시지로 에러 분류."""
    category = "unknown"
    pattern = "unknown"

    combined = f"{error_str} {json.dumps(response, ensure_ascii=False) if response else ''}"

    # Infra errors
    if status_code == 504 or "타임아웃" in combined or "timeout" in combined.lower():
        return "infra", "agent_timeout"
    if "ConnectionError" in combined or "Connection aborted" in combined:
        return "infra", "agent_timeout"
    if "RemoteDisconnected" in combined:
        return "infra", "agent_timeout"

    # Code errors (app bugs)
    if status_code == 500:
        if "traceback" in combined.lower() or "Traceback" in combined:
            return "code", "app_crash"
        return "code", "internal_error"
    if status_code == 422:
        return "code", "parse_error"

    # Scenario errors (LLM output problems)
    if status_code == 400:
        validation_errors = response.get("validation_errors", []) if response else []
        if any("알람" in e or "alarm" in e.lower() for e in validation_errors):
            return "scenario", "alarm_not_found"
        if any("rubric" in e.lower() or "weight" in e.lower() for e in validation_errors):
            return "scenario", "rubric_invalid"
        if any("variable" in e.lower() or "변수" in e for e in validation_errors):
            return "scenario", "undefined_variable"
        if any("fis" in e.lower() or "template" in e.lower() for e in validation_errors):
            return "scenario", "fis_template_error"
        if any("phase" in e.lower() or "단계" in e for e in validation_errors):
            return "scenario", "phase_structure_error"
        return "scenario", "validation_failed"

    # From response content
    if response and not response.get("ok"):
        err_msg = response.get("error", "")
        if "시나리오 검증 실패" in err_msg:
            return "scenario", "validation_failed"
        if "not found" in err_msg.lower():
            return "code", "not_found"

    # Success but scenario has issues (detected post-hoc)
    if response and response.get("ok") and response.get("scenario"):
        scenario = response["scenario"]
        if not scenario.get("evaluation_rubric"):
            return "scenario", "rubric_missing"

    return category, pattern


def classify_execution_error(status_code, run_data, error_str=""):
    """실행 결과로 에러 분류."""
    combined = f"{error_str} {json.dumps(run_data, ensure_ascii=False) if run_data else ''}"

    if status_code == 504 or "timeout" in combined.lower():
        return "infra", "agent_timeout"
    if status_code == 500:
        return "code", "executor_crash"
    if status_code == 404:
        return "code", "scenario_not_found"

    if run_data:
        status = run_data.get("status", "")
        result = run_data.get("result", "")
        steps = run_data.get("steps", [])
        error_detail = run_data.get("error", "")

        if status == "error" or result == "error":
            if "permission" in combined.lower() or "AccessDenied" in combined:
                return "infra", "missing_permissions"
            if "not found" in combined.lower() or "does not exist" in combined.lower():
                return "infra", "resource_not_found"
            if "timeout" in combined.lower():
                return "infra", "agent_timeout"
            if "syntax" in combined.lower() or "SyntaxError" in combined:
                return "scenario", "script_syntax_error"
            if "ImportError" in combined or "ModuleNotFoundError" in combined:
                return "code", "missing_dependency"

            # Check individual steps for patterns
            for step in steps:
                step_err = step.get("error", "") or step.get("detail", "")
                if "alarm" in step_err.lower() and "not found" in step_err.lower():
                    return "scenario", "alarm_not_found"
                if "ALARM" not in str(step.get("result", "")):
                    if step.get("type") == "metric_check":
                        return "scenario", "trigger_effect_mismatch"

            return "code", "executor_error"

        if result == "fail":
            # Execution completed but verification failed
            failed_steps = [s for s in steps if s.get("status") == "fail"]
            if any(s.get("type") == "metric_check" for s in failed_steps):
                return "scenario", "trigger_effect_mismatch"
            if any("alarm" in s.get("error", "").lower() for s in failed_steps):
                return "scenario", "alarm_not_found"
            return "scenario", "verification_failed"

    return "unknown", "unknown"


# ---------------------------------------------------------------------------
# Phase 1: Generation
# ---------------------------------------------------------------------------

def generate_scenario(fm_id: str, fm_name: str) -> GenerationResult:
    """POST /api/scenario-chat로 시나리오 생성."""
    result = GenerationResult(fm_id=fm_id, fm_name=fm_name)
    start = time.time()

    try:
        # Step 1: Generate scenario via chat
        message = (
            f"{fm_id} ({fm_name}) 장애 모드로 시나리오를 생성해줘. "
            f"현재 아키텍처에 맞는 구체적인 실행 가능 시나리오 JSON을 만들어줘."
        )
        status, resp = _post("/api/scenario-chat", {
            "message": message,
            "space_id": SPACE_ID,
            "template_id": fm_id,
            "include_script": False,
        }, timeout=GENERATION_TIMEOUT)

        if status != 200 or not resp.get("ok"):
            result.error = resp.get("error", f"HTTP {status}")
            result.error_category, result.error_pattern = classify_generation_error(
                status, resp, result.error)
            if resp.get("validation_errors"):
                result.validation_errors = resp["validation_errors"]
            result.duration = time.time() - start
            return result

        scenario = resp.get("scenario")
        if not scenario:
            result.error = "Agent responded but no scenario JSON extracted"
            result.error_category = "scenario"
            result.error_pattern = "no_json_output"
            result.duration = time.time() - start
            return result

        result.scenario = scenario
        result.scenario_id = scenario.get("id", "")
        result.fixes_applied = resp.get("fixes", [])

        # Step 2: Save scenario
        save_status, save_resp = _post("/api/arch/save-scenario", {
            "scenario": scenario,
            "space_id": SPACE_ID,
        }, timeout=30)

        if save_status == 409:
            # Already exists, delete and re-save
            _del_status, _ = requests.delete(
                f"{_base()}/api/scenarios/{result.scenario_id}",
                params={"space_id": SPACE_ID}, timeout=10
            ).status_code, None
            save_status, save_resp = _post("/api/arch/save-scenario", {
                "scenario": scenario,
                "space_id": SPACE_ID,
            }, timeout=30)

        if save_status != 200 or not save_resp.get("ok"):
            result.error = f"Save failed: {save_resp.get('error', '')} | validation: {save_resp.get('validation_errors', [])}"
            result.error_category, result.error_pattern = classify_generation_error(
                save_status, save_resp, result.error)
            result.validation_errors = save_resp.get("validation_errors", [])
            result.duration = time.time() - start
            return result

        # Step 3: Generate execution script
        script_status, script_resp = _post("/api/scenario-generate-script", {
            "scenario_id": result.scenario_id,
            "space_id": SPACE_ID,
            "script_type": "python",
        }, timeout=SCRIPT_GEN_TIMEOUT)

        if script_status == 200 and script_resp.get("ok"):
            result.script_generated = True
            result.script_dry_run = script_resp.get("dry_run", "unknown")
        else:
            # Script gen failure is non-fatal for generation phase
            result.script_generated = False
            result.error = f"Script gen: {script_resp.get('error', f'HTTP {script_status}')}"
            result.error_category, result.error_pattern = classify_generation_error(
                script_status, script_resp, result.error)
            result.duration = time.time() - start
            return result

        result.success = True

    except requests.exceptions.Timeout:
        result.error = "Request timeout"
        result.error_category = "infra"
        result.error_pattern = "agent_timeout"
    except requests.exceptions.ConnectionError as e:
        result.error = f"Connection error: {e}"
        result.error_category = "infra"
        result.error_pattern = "agent_timeout"
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        result.error_category = "code"
        result.error_pattern = "app_crash"

    result.duration = time.time() - start
    return result


# ---------------------------------------------------------------------------
# Phase 2: Execution
# ---------------------------------------------------------------------------

def execute_scenario(fm_id: str, scenario_id: str) -> ExecutionResult:
    """POST /api/scenario-run/{id}로 시나리오 실행 + 폴링."""
    result = ExecutionResult(fm_id=fm_id, scenario_id=scenario_id)
    start = time.time()

    try:
        # Start execution (space_id via query param)
        r = requests.post(
            f"{_base()}/api/scenario-run/{scenario_id}",
            params={"space_id": SPACE_ID},
            timeout=30,
        )
        status = r.status_code
        resp = r.json()

        if status != 200 or not resp.get("ok"):
            result.error = resp.get("error", f"HTTP {status}")
            result.error_category, result.error_pattern = classify_execution_error(
                status, resp, result.error)
            result.duration = time.time() - start
            return result

        result.run_id = resp.get("run_id", "")

        # Poll for completion (retry-resilient)
        deadline = time.time() + EXECUTION_TIMEOUT
        consecutive_errors = 0
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            try:
                poll_status, poll_resp = _get(
                    f"/api/scenario-run/{result.run_id}/status",
                    params={"space_id": SPACE_ID},
                )
                consecutive_errors = 0
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                consecutive_errors += 1
                if consecutive_errors >= 5:
                    result.error = "App unreachable (5 consecutive poll failures)"
                    result.error_category = "infra"
                    result.error_pattern = "app_unreachable"
                    break
                continue

            if poll_status != 200:
                continue

            run_status = poll_resp.get("status", "")
            if run_status in ("completed", "done", "pass", "fail", "error", "interrupted", "cancelled"):
                result.status = run_status
                result.result = poll_resp.get("result", run_status)
                result.steps_detail = poll_resp.get("steps", [])

                if result.result in ("pass", "completed"):
                    result.success = True
                else:
                    result.error = poll_resp.get("error", f"Result: {result.result}")
                    result.error_category, result.error_pattern = classify_execution_error(
                        200, poll_resp, result.error)
                break
        else:
            result.error = f"Execution timeout ({EXECUTION_TIMEOUT}s)"
            result.error_category = "infra"
            result.error_pattern = "execution_timeout"

    except requests.exceptions.Timeout:
        result.error = "Request timeout starting run"
        result.error_category = "infra"
        result.error_pattern = "agent_timeout"
    except requests.exceptions.ConnectionError as e:
        result.error = f"Connection error starting run: {e}"
        result.error_category = "infra"
        result.error_pattern = "agent_timeout"
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        result.error_category = "code"
        result.error_pattern = "executor_crash"

    result.duration = time.time() - start
    return result


# ---------------------------------------------------------------------------
# Phase 3: Analysis & Report
# ---------------------------------------------------------------------------

def aggregate_root_causes(gen_results: list, exec_results: list) -> list:
    """패턴별로 그루핑하여 근본 원인 도출."""
    pattern_map = {}

    for r in gen_results:
        if not r.success and r.error_pattern:
            key = f"gen:{r.error_pattern}"
            if key not in pattern_map:
                pattern_map[key] = RootCause(
                    pattern=r.error_pattern,
                    category=r.error_category,
                )
            pattern_map[key].affected_fms.append(r.fm_id)

    for r in exec_results:
        if not r.success and r.error_pattern:
            key = f"exec:{r.error_pattern}"
            if key not in pattern_map:
                pattern_map[key] = RootCause(
                    pattern=r.error_pattern,
                    category=r.error_category,
                )
            pattern_map[key].affected_fms.append(r.fm_id)

    # Add descriptions and fix suggestions
    FIX_MAP = {
        "agent_timeout": ("Agent Space 응답 없음/타임아웃", "Agent Space 상태 확인, Flask timeout 증가"),
        "alarm_not_found": ("LLM이 존재하지 않는 알람 이름 사용", "프롬프트에 가용 알람 목록 더 강조, few-shot 예시 추가"),
        "trigger_effect_mismatch": ("trigger가 metric 변화를 유발하지 못함", "FIS duration vs alarm evaluation window 검증 강화"),
        "rubric_invalid": ("evaluation_rubric weight 합계 != 100", "프롬프트 강화 또는 post-processing 자동 보정"),
        "rubric_missing": ("evaluation_rubric 필드 누락", "post-processing에서 자동 생성"),
        "undefined_variable": ("시나리오에 미선언 변수 사용", "variables discovery 메커니즘 프롬프트에 강조"),
        "fis_template_error": ("FIS 템플릿 ID 미스매치/존재하지 않음", "available FIS templates 목록을 명시적으로 전달"),
        "validation_failed": ("시나리오 JSON 구조/내용 검증 실패", "검증 규칙 분석 후 프롬프트 조정 필요"),
        "no_json_output": ("Agent가 JSON 없이 텍스트만 응답", "프롬프트 마지막에 JSON 출력 강제 지시 추가"),
        "app_crash": ("Flask 앱 크래시 (500)", "에러 로그 확인 후 코드 수정"),
        "script_syntax_error": ("Agent 생성 steps.py 구문 오류", "dry-run 검증 + fix loop 강화"),
        "executor_crash": ("ScriptExecutor 실행 중 크래시", "executor 에러 핸들링 강화"),
        "missing_permissions": ("IAM 권한 부족", "FIS/CloudWatch 관련 IAM policy 추가"),
        "execution_timeout": ("실행이 제한 시간 초과", "시나리오 duration 줄이거나 timeout 증가"),
        "phase_structure_error": ("verification phase 구조 오류", "phase 규칙 프롬프트 강화"),
        "parse_error": ("응답 파싱 실패", "Agent 응답 형식 제약 강화"),
        "resource_not_found": ("대상 AWS/K8s 리소스 없음", "readiness probe 결과 반영"),
        "verification_failed": ("시나리오 실행 완료했으나 검증 실패", "trigger↔effect 인과관계 분석 필요"),
    }

    for rc in pattern_map.values():
        desc, fix = FIX_MAP.get(rc.pattern, ("", ""))
        rc.description = desc
        rc.fix_suggestion = fix

    # Sort by impact (number of affected FMs)
    return sorted(pattern_map.values(), key=lambda x: len(x.affected_fms), reverse=True)


def build_report(gen_results: list, exec_results: list, root_causes: list, elapsed: float) -> dict:
    """최종 리포트 JSON 생성."""
    gen_ok = sum(1 for r in gen_results if r.success)
    exec_ok = sum(1 for r in exec_results if r.success)

    by_category = {}
    for r in gen_results:
        if not r.success:
            cat = r.error_category or "unknown"
            by_category[cat] = by_category.get(cat, 0) + 1
    for r in exec_results:
        if not r.success:
            cat = r.error_category or "unknown"
            by_category[cat] = by_category.get(cat, 0) + 1

    results = []
    exec_map = {r.fm_id: r for r in exec_results}
    for gr in gen_results:
        entry = {
            "fm_id": gr.fm_id,
            "fm_name": gr.fm_name,
            "generation": {
                "success": gr.success,
                "scenario_id": gr.scenario_id,
                "script_generated": gr.script_generated,
                "script_dry_run": gr.script_dry_run,
                "duration_s": round(gr.duration, 1),
                "error": gr.error or None,
                "error_category": gr.error_category or None,
                "error_pattern": gr.error_pattern or None,
                "validation_errors": gr.validation_errors or None,
                "fixes_applied": gr.fixes_applied or None,
            },
        }
        er = exec_map.get(gr.fm_id)
        if er:
            entry["execution"] = {
                "success": er.success,
                "run_id": er.run_id,
                "status": er.status,
                "result": er.result,
                "duration_s": round(er.duration, 1),
                "error": er.error or None,
                "error_category": er.error_category or None,
                "error_pattern": er.error_pattern or None,
                "steps_summary": [
                    {"name": s.get("name", ""), "status": s.get("status", ""), "type": s.get("type", "")}
                    for s in (er.steps_detail or [])
                ] or None,
            }
        results.append(entry)

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(elapsed, 1),
        "summary": {
            "total": len(gen_results),
            "gen_ok": gen_ok,
            "gen_fail": len(gen_results) - gen_ok,
            "exec_attempted": len(exec_results),
            "exec_ok": exec_ok,
            "exec_fail": len(exec_results) - exec_ok,
            "by_category": by_category,
        },
        "results": results,
        "root_causes": [asdict(rc) for rc in root_causes],
        "recommendations": _build_recommendations(root_causes),
    }


def _build_recommendations(root_causes: list) -> list:
    """근본 원인에서 우선순위 권고 생성."""
    recs = []
    for i, rc in enumerate(root_causes):
        if not rc.fix_suggestion:
            continue
        recs.append({
            "priority": i + 1,
            "pattern": rc.pattern,
            "category": rc.category,
            "affected_count": len(rc.affected_fms),
            "affected_fms": rc.affected_fms,
            "action": rc.fix_suggestion,
            "impact": f"{len(rc.affected_fms)}개 시나리오 영향",
        })
    return recs


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_header(title: str):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


def print_generation_progress(result: GenerationResult):
    icon = "✓" if result.success else "✗"
    extra = ""
    if not result.success:
        extra = f" [{result.error_category}:{result.error_pattern}]"
    print(f"  [{result.fm_id}] {result.fm_name:<35} {icon} ({result.duration:.1f}s){extra}")


def print_execution_progress(result: ExecutionResult):
    icon = "✓" if result.success else "✗"
    extra = ""
    if not result.success:
        extra = f" [{result.error_category}:{result.error_pattern}]"
    print(f"  [{result.fm_id}] {result.scenario_id:<40} {icon} ({result.duration:.1f}s){extra}")


def print_summary(report: dict):
    s = report["summary"]
    print_header("결과 요약")
    print(f"  생성: {s['gen_ok']}/{s['total']} 통과 | 실행: {s['exec_ok']}/{s['exec_attempted']} 통과")
    print(f"  에러 분류: {json.dumps(s['by_category'], ensure_ascii=False)}")
    print()

    # Results table
    print(f"  {'FM':<6} {'Name':<30} {'Gen':>3} {'Script':>6} {'Exec':>4} {'Category':<12} {'Pattern':<25} {'Time':>5}")
    print(f"  {'-'*6} {'-'*30} {'-'*3} {'-'*6} {'-'*4} {'-'*12} {'-'*25} {'-'*5}")

    for r in report["results"]:
        gen = r["generation"]
        exe = r.get("execution", {})
        gen_icon = "✓" if gen["success"] else "✗"
        script_icon = "✓" if gen.get("script_generated") else "-"
        exec_icon = "✓" if exe.get("success") else ("✗" if exe else "-")
        cat = gen.get("error_category") or exe.get("error_category") or "-"
        pat = gen.get("error_pattern") or exe.get("error_pattern") or "-"
        total_time = gen["duration_s"] + (exe.get("duration_s", 0) or 0)
        print(f"  {r['fm_id']:<6} {r['fm_name']:<30} {gen_icon:>3} {script_icon:>6} {exec_icon:>4} {cat:<12} {pat:<25} {total_time:>4.0f}s")

    # Root causes
    if report["root_causes"]:
        print_header("근본 원인 (Root Causes)")
        for i, rc in enumerate(report["root_causes"], 1):
            print(f"  [{i}] {rc['pattern']} ({rc['category']}) — {len(rc['affected_fms'])}개 FM 영향")
            print(f"      설명: {rc['description']}")
            print(f"      대상: {', '.join(rc['affected_fms'])}")
            print(f"      수정: {rc['fix_suggestion']}")
            print()

    # Recommendations
    if report["recommendations"]:
        print_header("우선순위 권고")
        for rec in report["recommendations"]:
            print(f"  P{rec['priority']}: [{rec['category']}] {rec['action']}")
            print(f"       → {rec['affected_count']}개 시나리오 해결: {', '.join(rec['affected_fms'])}")
            print()


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def load_failure_modes():
    """failure_modes.py에서 FM 목록 로드."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "services" / "dashboard"))
    from failure_modes import FAILURE_MODES
    return {fm["id"]: fm["name"] for fm in FAILURE_MODES}


def health_check():
    """앱 헬스체크."""
    try:
        status, resp = _get("/api/scenarios", params={"space_id": SPACE_ID})
        if status == 200 and resp.get("ok"):
            return True, f"{len(resp.get('scenarios', []))} scenarios registered"
        return False, f"HTTP {status}: {resp}"
    except Exception as e:
        return False, str(e)


def run_phase_generate(fm_ids: list, fm_names: dict) -> list:
    """Phase 1: 순차 시나리오 생성."""
    print_header(f"Phase 1: GENERATION ({len(fm_ids)} failure modes)")
    results = []

    for i, fm_id in enumerate(fm_ids, 1):
        name = fm_names.get(fm_id, fm_id)
        print(f"  [{i}/{len(fm_ids)}] {fm_id} - {name} ...", end=" ", flush=True)
        result = generate_scenario(fm_id, name)
        results.append(result)

        icon = "✓" if result.success else "✗"
        extra = f" [{result.error_category}:{result.error_pattern}]" if not result.success else ""
        print(f"{icon} ({result.duration:.1f}s){extra}")

        # Save intermediate result
        _save_intermediate(results, [], "generating")

    return results


def run_phase_execute(gen_results: list) -> list:
    """Phase 2: 순차 시나리오 실행."""
    runnable = [r for r in gen_results if r.success and r.scenario_id]
    print_header(f"Phase 2: EXECUTION ({len(runnable)}/{len(gen_results)} runnable)")

    if not runnable:
        print("  실행 가능한 시나리오 없음 (Phase 1 모두 실패)")
        return []

    results = []
    for i, gr in enumerate(runnable, 1):
        print(f"  [{i}/{len(runnable)}] {gr.fm_id} — {gr.scenario_id} ...", end=" ", flush=True)
        result = execute_scenario(gr.fm_id, gr.scenario_id)
        results.append(result)

        icon = "✓" if result.success else "✗"
        extra = f" [{result.error_category}:{result.error_pattern}]" if not result.success else ""
        print(f"{icon} ({result.duration:.1f}s){extra}")

        # Cooldown between executions
        if i < len(runnable):
            print(f"  ... {COOLDOWN}s 쿨다운 (알람 안정화)")
            time.sleep(COOLDOWN)

        _save_intermediate(gen_results, results, "executing")

    return results


def _save_intermediate(gen_results, exec_results, phase):
    """중간 결과 저장 (crash 시 복구 용도)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "phase": phase,
        "timestamp": datetime.now().isoformat(),
        "generation": [asdict(r) for r in gen_results],
        "execution": [asdict(r) for r in exec_results],
    }
    with open(OUTPUT_DIR / "intermediate.json", "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_intermediate() -> tuple:
    """이전 중간 결과 로드 (--phase execute 용)."""
    path = OUTPUT_DIR / "intermediate.json"
    if not path.exists():
        return [], []
    with open(path) as f:
        data = json.load(f)
    gen_results = []
    for d in data.get("generation", []):
        r = GenerationResult(fm_id=d["fm_id"])
        for k, v in d.items():
            if hasattr(r, k):
                setattr(r, k, v)
        gen_results.append(r)
    return gen_results, data.get("execution", [])


def main():
    parser = argparse.ArgumentParser(description="16 FM 전체 자동 검증")
    parser.add_argument("--phase", choices=["generate", "execute", "all"], default="all")
    parser.add_argument("--modes", help="FM IDs (comma-sep)", default="")
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--url", default=_CONFIG["base_url"])
    args = parser.parse_args()

    set_base_url(args.url)

    # Determine FM list
    fm_names = load_failure_modes()
    if args.modes:
        fm_ids = [m.strip() for m in args.modes.split(",")]
    else:
        fm_ids = ALL_FM_IDS

    # Header
    print_header(f"시나리오 생성 + 실행 전체 검증 ({len(fm_ids)} failure modes)")
    print(f"  URL: {_base()}")
    print(f"  Space: {SPACE_ID}")
    print(f"  Phase: {args.phase}")
    print(f"  FM: {', '.join(fm_ids)}")

    # Health check
    ok, msg = health_check()
    if not ok:
        print(f"\n  ❌ 헬스체크 실패: {msg}")
        print("  앱이 실행 중인지 확인하세요.")
        sys.exit(1)
    print(f"  ✓ 헬스체크 통과: {msg}\n")

    start_time = time.time()
    gen_results = []
    exec_results = []

    # Phase 1
    if args.phase in ("generate", "all"):
        gen_results = run_phase_generate(fm_ids, fm_names)

    # Load from intermediate if execute-only
    if args.phase == "execute":
        gen_results, _ = load_intermediate()
        if not gen_results:
            print("  이전 생성 결과 없음. --phase generate 먼저 실행하세요.")
            sys.exit(1)

    # Phase 2
    if args.phase in ("execute", "all"):
        exec_results = run_phase_execute(gen_results)

    # Phase 3: Report
    elapsed = time.time() - start_time
    root_causes = aggregate_root_causes(gen_results, exec_results)
    report = build_report(gen_results, exec_results, root_causes, elapsed)

    # Save report
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n  리포트 저장: {REPORT_PATH}")

    # Print summary
    print_summary(report)

    # Exit code: 0 if all passed, 1 if any failures
    total_failures = (len(gen_results) - sum(1 for r in gen_results if r.success)) + \
                     (len(exec_results) - sum(1 for r in exec_results if r.success))
    sys.exit(0 if total_failures == 0 else 1)


if __name__ == "__main__":
    main()
