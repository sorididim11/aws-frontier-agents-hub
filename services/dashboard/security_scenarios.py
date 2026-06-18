"""
Security Scenarios — Finding → 실행 가능한 시나리오 변환 + 실행.

Security Agent의 attackScript를 파싱하여 DevOps 시나리오 포맷으로 변환하고,
ScenarioContext를 사용하여 실행. 재검증(PR merge 후)과 회귀 테스트 모두 포함.

사용:
    from security_scenarios import SecurityScenarioEngine
    engine = SecurityScenarioEngine()
    scenario = engine.convert_finding(finding)
    result = engine.execute(scenario)
"""
import json
import os
import re
import time
import threading
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from topology_provider import topology, ServiceLocation
from scenario_runner import ScenarioContext

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results", "security")
REGISTERED_DIR = os.path.join(os.path.dirname(__file__), "scenarios", "security")


@dataclass
class AttackStep:
    """파싱된 공격 단계."""
    method: str = "GET"
    path: str = "/"
    headers: dict = field(default_factory=dict)
    body: Optional[str] = None
    expected_status: Optional[int] = None
    description: str = ""

    _VALID_FIELDS = frozenset(["method", "path", "headers", "body", "expected_status", "description"])

    @classmethod
    def from_dict(cls, d: dict) -> "AttackStep":
        filtered = {k: v for k, v in d.items() if k in cls._VALID_FIELDS}
        return cls(**filtered)


@dataclass
class SecurityScenario:
    """변환된 보안 시나리오."""
    id: str = ""
    finding_id: str = ""
    finding_name: str = ""
    risk_type: str = ""
    risk_level: str = ""
    service_name: str = ""
    endpoint: str = ""
    attack_steps: list = field(default_factory=list)
    created_at: str = ""


@dataclass
class StepResult:
    """개별 attack step 실행 결과."""
    index: int = 0
    method: str = ""
    path: str = ""
    status_code: int = 0
    body_snippet: str = ""
    vuln_pattern_found: bool = False
    duration: float = 0.0
    error: str = ""


@dataclass
class ExecutionResult:
    """시나리오 실행 결과."""
    scenario_id: str = ""
    finding_id: str = ""
    status: str = ""  # "vulnerable" | "defended" | "error"
    original_response: Optional[int] = None
    current_response: Optional[int] = None
    response_body_snippet: str = ""
    vulnerability_pattern_found: bool = False
    executed_at: str = ""
    duration: float = 0.0
    detail: str = ""
    steps: list = field(default_factory=list)


RISK_TYPE_LAYER = {
    "XSS": "app",
    "SQL_INJECTION": "app",
    "PATH_TRAVERSAL": "app",
    "COMMAND_INJECTION": "app",
    "INSECURE_DESERIALIZATION": "app",
    "SSRF": "app+infra",
    "SECURITY_MISCONFIGURATION": "infra",
    "HEADER_EXPOSURE": "infra",
    "PRIVILEGE_ESCALATION": "app+infra",
    "INFO_DISCLOSURE": "app",
}


class SecurityScenarioEngine:
    """Finding → Scenario 변환 + 실행 엔진."""

    VULN_PATTERNS = {
        "INFO_DISCLOSURE": [r"stack\s*trace", r"traceback", r"exception", r"\.py\"|\.java\"", r"at\s+\w+\.\w+\("],
        "XSS": [r"<script", r"javascript:", r"onerror=", r"onload="],
        "SQL_INJECTION": [r"sql\s*error", r"syntax\s*error", r"mysql", r"postgresql", r"sqlite"],
        "COMMAND_INJECTION": [r"sh:", r"bash:", r"command\s*not\s*found", r"permission\s*denied"],
        "SSRF": [r"connection\s*refused", r"internal\s*server", r"169\.254"],
        "HEADER_EXPOSURE": [r"x-powered-by", r"server:", r"x-aspnet-version"],
    }

    def __init__(self):
        self._runs: dict[str, ExecutionResult] = {}
        self._lock = threading.Lock()
        os.makedirs(RESULTS_DIR, exist_ok=True)

    # ══════════════════════════════════════════════════════════════════
    # Conversion: Finding → SecurityScenario
    # ══════════════════════════════════════════════════════════════════

    def convert_finding(self, finding: dict) -> SecurityScenario:
        """Security Agent finding을 실행 가능한 시나리오로 변환."""
        finding_id = finding.get("id", "")
        attack_script = finding.get("attackScript", "")
        endpoint = finding.get("endpoint", "") or ""
        service_name = self._extract_service(endpoint)

        steps = self._parse_attack_script(attack_script)
        if not steps and endpoint:
            steps = [AttackStep(method="GET", path="/", description="기본 엔드포인트 접근")]

        return SecurityScenario(
            id=f"SEC-{finding_id[:8]}",
            finding_id=finding_id,
            finding_name=finding.get("name", ""),
            risk_type=finding.get("riskType", ""),
            risk_level=finding.get("riskLevel", ""),
            service_name=service_name,
            endpoint=endpoint,
            attack_steps=[asdict(s) for s in steps],
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def convert_findings(self, findings: list[dict]) -> list[SecurityScenario]:
        """여러 findings를 일괄 변환."""
        return [self.convert_finding(f) for f in findings]

    # ══════════════════════════════════════════════════════════════════
    # Execution: SecurityScenario → ExecutionResult
    # ══════════════════════════════════════════════════════════════════

    def execute(self, scenario: SecurityScenario, port: int = 18080, on_step=None) -> ExecutionResult:
        """시나리오 실행 — attackScript 재실행 후 취약 여부 판정.
        on_step(StepResult) 콜백으로 각 step 진행 상황 실시간 전달.
        """
        start = time.time()
        result = ExecutionResult(
            scenario_id=scenario.id,
            finding_id=scenario.finding_id,
            executed_at=datetime.now(timezone.utc).isoformat(),
        )

        loc = topology.resolve(scenario.service_name) if scenario.service_name else None
        # endpoint 없고 topology도 못 찾지만 attack_steps가 있으면 → 자기 자신(dashboard) 공격
        if not loc and not scenario.endpoint:
            if scenario.attack_steps:
                from flask import request as _req
                try:
                    scenario.endpoint = _req.host_url.rstrip("/")
                except RuntimeError:
                    scenario.endpoint = "http://localhost:5003"
            else:
                result.status = "error"
                result.detail = f"서비스 '{scenario.service_name}' 토폴로지에서 찾을 수 없음"
                result.duration = time.time() - start
                return result

        step_results = []

        def _collect_step(sr):
            step_results.append(sr)
            if on_step:
                on_step(sr)

        try:
            exec_result = None

            # endpoint가 있으면 직접 실행 시도
            if scenario.endpoint:
                exec_result = self._execute_direct(scenario, on_step=_collect_step)
                # 연결 실패(HTTP 0) → fallback
                if exec_result.get("status_code") == 0:
                    step_results.clear()
                    if loc:
                        ctx = ScenarioContext(
                            namespace=loc.namespace,
                            kubectl_context=loc.context,
                        )
                        exec_result = self._execute_with_port_forward(ctx, scenario, loc, port, on_step=_collect_step)
                    elif scenario.attack_steps:
                        # DNS 불가 → localhost로 재시도 (자기 자신에 대한 pentest)
                        from flask import request as _req
                        try:
                            scenario.endpoint = _req.host_url.rstrip("/")
                        except RuntimeError:
                            scenario.endpoint = "http://localhost:5003"
                        exec_result = self._execute_direct(scenario, on_step=_collect_step)
            elif loc:
                ctx = ScenarioContext(
                    namespace=loc.namespace,
                    kubectl_context=loc.context,
                )
                exec_result = self._execute_with_port_forward(ctx, scenario, loc, port, on_step=_collect_step)
            else:
                exec_result = {"status_code": 0, "body": "", "headers": {}}

            result.steps = [asdict(sr) for sr in step_results]
            result.current_response = exec_result.get("status_code")
            result.response_body_snippet = exec_result.get("body", "")[:500]
            result.vulnerability_pattern_found = self._check_vuln_patterns(
                exec_result.get("body", ""),
                exec_result.get("headers", {}),
                scenario.risk_type,
            )

            # HTTP 0 = 연결 불가 → 테스트 불가 (defended 아님)
            if result.current_response == 0:
                result.status = "error"
                result.detail = "대상 서비스 접근 불가 (DNS/네트워크 실패)"
            elif result.vulnerability_pattern_found or (result.current_response and 200 <= result.current_response < 400):
                result.status = "vulnerable"
                result.detail = "취약점 여전히 존재 (공격 성공)"
            elif result.current_response in (403, 404, 503):
                result.status = "defended"
                result.detail = f"방어됨 (HTTP {result.current_response})"
            else:
                result.status = "defended"
                result.detail = f"응답 변경됨 (HTTP {result.current_response})"

        except Exception as e:
            result.status = "error"
            result.detail = str(e)

        result.duration = time.time() - start
        self._save_result(result)
        return result

    def execute_async(self, scenario: SecurityScenario, port: int = 18080) -> str:
        """비동기 실행. run_id 반환."""
        run_id = f"secrun-{scenario.finding_id[:8]}-{int(time.time())}"

        def _run():
            res = self.execute(scenario, port)
            with self._lock:
                self._runs[run_id] = res

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return run_id

    def get_result(self, run_id: str) -> Optional[ExecutionResult]:
        with self._lock:
            return self._runs.get(run_id)

    def list_results(self) -> list[dict]:
        """저장된 결과 목록."""
        results = []
        if not os.path.exists(RESULTS_DIR):
            return results
        for f in sorted(os.listdir(RESULTS_DIR), reverse=True)[:50]:
            if f.endswith(".json"):
                try:
                    with open(os.path.join(RESULTS_DIR, f)) as fp:
                        results.append(json.load(fp))
                except Exception:
                    pass
        return results

    # ══════════════════════════════════════════════════════════════════
    # Re-verification: PR merge 후 재검증
    # ══════════════════════════════════════════════════════════════════

    def reverify_finding(self, finding: dict, port: int = 18080) -> ExecutionResult:
        """PR merge 후 finding 재검증 — attackScript 재실행 + 비교."""
        scenario = self.convert_finding(finding)
        result = self.execute(scenario, port)

        result.original_response = finding.get("original_response_code")
        if result.status == "defended":
            result.detail = "수정 확인됨 (PR merge 후 재검증 통과)"
        elif result.status == "vulnerable":
            result.detail = "수정 미확인 (PR merge 후에도 여전히 취약)"

        return result

    # ══════════════════════════════════════════════════════════════════
    # Internal
    # ══════════════════════════════════════════════════════════════════

    def _execute_with_port_forward(self, ctx: ScenarioContext,
                                   scenario: SecurityScenario,
                                   loc: ServiceLocation,
                                   port: int, on_step=None) -> dict:
        """port-forward를 통해 공격 실행."""
        last_result = {"status_code": 0, "body": "", "headers": {}}
        with ctx.port_forward(loc.service_name, port) as pf:
            base_url = pf.url
            for idx, step_data in enumerate(scenario.attack_steps):
                step = AttackStep.from_dict(step_data) if isinstance(step_data, dict) else step_data
                url = f"{base_url}{step.path}"
                step_start = time.time()
                sr = StepResult(index=idx, method=step.method, path=step.path)
                status, body = ctx.curl(url, method=step.method, data=step.body, timeout=10)
                sr.status_code = status
                sr.body_snippet = body[:300] if body else ""
                sr.duration = time.time() - step_start
                last_result = {"status_code": status, "body": body or "", "headers": {}}
                if on_step:
                    on_step(sr)
        return last_result

    # Security Agent가 headers에 메타데이터를 넣는 비정상 키 목록
    _META_HEADER_KEYS = {"Body", "Response", "Verification", "Content-Type"}

    def _execute_direct(self, scenario: SecurityScenario, on_step=None) -> dict:
        """직접 엔드포인트로 공격 실행 (port-forward 불필요).
        on_step(StepResult) 콜백으로 각 step 진행 상황 전달.
        """
        endpoint = scenario.endpoint
        last_result = {"status_code": 0, "body": "", "headers": {}}

        for idx, step_data in enumerate(scenario.attack_steps):
            step = AttackStep.from_dict(step_data) if isinstance(step_data, dict) else step_data
            url = f"{endpoint.rstrip('/')}{step.path}"
            step_start = time.time()
            sr = StepResult(index=idx, method=step.method, path=step.path)

            try:
                # Security Agent 포맷: headers["Body"]가 실제 HTTP payload, step.body는 설명문
                actual_body = ""
                actual_headers = {}
                for k, v in step.headers.items():
                    if k == "Body":
                        actual_body = v
                    elif k not in self._META_HEADER_KEYS:
                        actual_headers[k] = v
                if not actual_body and step.body and step.body.startswith("{"):
                    actual_body = step.body

                req = urllib.request.Request(url, method=step.method)
                send_body = actual_body if step.method in ("POST", "PUT", "PATCH") else ""
                if send_body and "Content-Type" not in actual_headers:
                    req.add_header("Content-Type", "application/json")
                for k, v in actual_headers.items():
                    req.add_header(k, v)
                if send_body:
                    req.data = send_body.encode("utf-8")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    headers = dict(resp.headers)
                    body = resp.read().decode()
                    sr.status_code = resp.status
                    sr.body_snippet = body[:300]
                    sr.vuln_pattern_found = self._check_vuln_patterns(body, headers, scenario.risk_type)
                    last_result = {"status_code": resp.status, "body": body, "headers": headers}
            except urllib.error.HTTPError as e:
                body = e.read().decode() if e.fp else ""
                sr.status_code = e.code
                sr.body_snippet = body[:300]
                sr.vuln_pattern_found = self._check_vuln_patterns(body, dict(e.headers) if e.headers else {}, scenario.risk_type)
                last_result = {"status_code": e.code, "body": body, "headers": dict(e.headers) if e.headers else {}}
            except Exception as ex:
                sr.error = str(ex)
                last_result = {"status_code": 0, "body": "", "headers": {}}

            sr.duration = time.time() - step_start
            if on_step:
                on_step(sr)

        return last_result

    def _parse_attack_script(self, script: str) -> list[AttackStep]:
        """attackScript 텍스트에서 HTTP 요청을 추출하여 AttackStep 목록으로 변환.

        Security Agent의 attackScript는 pentest 실행 로그 텍스트.
        지원 포맷:
          - "1. GET /path → HTTP 404"  (번호 리스트)
          - "GET /path → HTTP 200"     (인라인)
          - "POST /path HTTP/1.1"      (raw HTTP)
          - "Request: POST http://host/path" (curl 설명)
          - "curl -X POST http://host/path -d '...'" (curl 커맨드)
        """
        if not script:
            return []

        steps = []
        # 패턴: 선택적 번호 + HTTP method + URL/path
        # body 추출: "Body: {...}" 또는 "-d '{...}'" 또는 "body: ..."
        pattern = re.compile(
            r'(?:^|\n)\s*'
            r'(?:\d+[\.\)]\s*)?'  # 선택적 번호 (1. 또는 1))
            r'(?:Request:\s*)?'    # 선택적 "Request:" prefix
            r'(GET|POST|PUT|DELETE|PATCH)\s+'
            r'(https?://[^\s"<>]+|/[^\s"<>,→]+)',
            re.MULTILINE
        )

        body_pattern = re.compile(
            r'(?:Body|body|data)[\s:]*["\']?(\{[^}]+\})["\']?'
        )
        curl_body_pattern = re.compile(
            r"-d\s+['\"](.+?)['\"]"
        )

        for m in pattern.finditer(script):
            method = m.group(1)
            url_raw = m.group(2).rstrip(".,;:)→")
            path = re.sub(r'^https?://[^/]+', '', url_raw) or "/"

            # 이 매치 이후 ~ 다음 매치 전까지 텍스트에서 body 추출
            start = m.end()
            next_m = pattern.search(script, start)
            chunk = script[start:next_m.start()] if next_m else script[start:start + 300]

            body = None
            bm = body_pattern.search(chunk)
            if bm:
                body = bm.group(1)
            else:
                cm = curl_body_pattern.search(chunk)
                if cm:
                    body = cm.group(1)

            steps.append(AttackStep(method=method, path=path, body=body))

        return steps

    def _check_vuln_patterns(self, body: str, headers: dict, risk_type: str) -> bool:
        """응답에서 취약점 패턴 탐지."""
        patterns = self.VULN_PATTERNS.get(risk_type, [])
        combined = body + " " + " ".join(f"{k}: {v}" for k, v in headers.items())
        for pattern in patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                return True
        return False

    def _extract_service(self, endpoint: str) -> str:
        if not endpoint:
            return ""
        match = re.search(r'https?://([^/:]+)', endpoint)
        if not match:
            return ""
        host = match.group(1)
        return host.split(".")[0] if host else ""

    def _save_result(self, result: ExecutionResult):
        """실행 결과를 파일로 저장."""
        filename = f"{result.executed_at[:19].replace(':', '').replace('-', '')}_{result.finding_id[:8]}.json"
        filepath = os.path.join(RESULTS_DIR, filename)
        try:
            with open(filepath, "w") as f:
                json.dump(asdict(result), f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════
    # Registration: 시나리오 탭에 영구 등록
    # ══════════════════════════════════════════════════════════════════

    def register_scenario(self, finding: dict, space_app_name: str = "", devops_space_id: str = "") -> dict:
        """Finding을 시나리오 탭 호환 포맷으로 영구 등록."""
        os.makedirs(REGISTERED_DIR, exist_ok=True)

        scenario = self.convert_finding(finding)
        risk_type = finding.get("riskType", "")
        layer = RISK_TYPE_LAYER.get(risk_type, "app")

        endpoint = finding.get("endpoint", "") or ""
        path = "/"
        if scenario.attack_steps:
            step0 = scenario.attack_steps[0]
            if isinstance(step0, dict):
                path = step0.get("path", "/")

        topology_edges = [
            {"from": "attacker", "to": "alb", "label": "HTTP"},
            {"from": "alb", "to": scenario.service_name or "target", "label": f"{path}"},
        ]

        svc_name = space_app_name or scenario.service_name or "unknown"
        registered = {
            "id": scenario.id,
            "devops_space_id": devops_space_id,
            "name": finding.get("name", "") or risk_type,
            "description": finding.get("description", ""),
            "category": f"security-{svc_name}",
            "app_name": svc_name,
            "layer": layer,
            "target_service": svc_name,
            "failure_mode": risk_type,
            "purpose": "보안 취약점 회귀 테스트",
            "expected_root_cause": f"{risk_type} 취약점 — {finding.get('name', '')}",
            "source": "security-agent",
            "finding_id": finding.get("id", ""),
            "risk_level": finding.get("riskLevel", ""),
            "risk_type": risk_type,
            "endpoint": endpoint,
            "attack_steps": scenario.attack_steps,
            "topology_edges": topology_edges,
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "trigger": {
                "type": "security-attack",
                "command": f"# Attack: {risk_type} on {scenario.service_name}\n# Endpoint: {endpoint}{path}",
            },
            "verification": {
                "steps": [
                    {
                        "type": "security_check",
                        "description": f"{risk_type} 방어 확인",
                        "expected": "defended",
                    }
                ]
            },
        }

        fname = f"{scenario.id}.json" if not devops_space_id else f"{scenario.id}_{devops_space_id[:8]}.json"
        filepath = os.path.join(REGISTERED_DIR, fname)
        with open(filepath, "w") as f:
            json.dump(registered, f, indent=2, ensure_ascii=False)

        return registered

    def get_registered_scenario(self, finding_id: str, devops_space_id: str = "") -> Optional[dict]:
        """Finding ID로 등록된 시나리오 조회 (prefix 매칭 지원).
        devops_space_id가 주어지면 해당 space에 등록된 것만 반환."""
        if not os.path.exists(REGISTERED_DIR):
            return None
        for fname in os.listdir(REGISTERED_DIR):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(REGISTERED_DIR, fname)) as f:
                    data = json.load(f)
                stored_fid = data.get("finding_id", "")
                if stored_fid == finding_id or stored_fid.startswith(finding_id):
                    if devops_space_id and data.get("devops_space_id", "") != devops_space_id:
                        continue
                    last_result = self._get_latest_result_for(stored_fid)
                    if last_result:
                        data["last_result"] = last_result
                    return data
            except Exception:
                continue
        return None

    def list_registered_scenarios(self) -> list[dict]:
        """등록된 보안 시나리오 전체 목록."""
        results = []
        if not os.path.exists(REGISTERED_DIR):
            return results
        for fname in sorted(os.listdir(REGISTERED_DIR)):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(REGISTERED_DIR, fname)) as f:
                    data = json.load(f)
                last_result = self._get_latest_result_for(data.get("finding_id", ""))
                if last_result:
                    data["last_result"] = last_result
                results.append(data)
            except Exception:
                continue
        return results

    def _get_latest_result_for(self, finding_id: str) -> Optional[dict]:
        """Finding ID에 대한 최근 실행 결과."""
        if not finding_id or not os.path.exists(RESULTS_DIR):
            return None
        prefix = finding_id[:8]
        matching = []
        for fname in os.listdir(RESULTS_DIR):
            if fname.endswith(".json") and prefix in fname:
                matching.append(fname)
        if not matching:
            return None
        matching.sort(reverse=True)
        try:
            with open(os.path.join(RESULTS_DIR, matching[0])) as f:
                return json.load(f)
        except Exception:
            return None


# Module-level singleton
scenario_engine = SecurityScenarioEngine()
