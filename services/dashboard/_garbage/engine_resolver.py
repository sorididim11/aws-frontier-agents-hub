"""
Engine Resolver: eager variable resolution + resource existence validation.

Responsibilities:
- Resolve all ${VAR} variables upfront (before any execution)
- Validate referenced resources actually exist (alarms, deployments, log groups)
- Return clear, structured errors on failure (which variable, which command, why)

Dependency: _run_cmd, boto3, ExecutionContext. No reverse dependency.
"""
import os
import re
from dataclasses import dataclass, field

from verifier_utils import _run_cmd, _cfg, AWS_REGION, NAMESPACE


_PROJECT_NAME = _cfg("project.name", os.environ.get("PROJECT_NAME", "frontier-agent-hub"))


@dataclass
class VarFailure:
    key: str
    command: str
    error: str

    def __str__(self):
        return f"${{{self.key}}}: cmd='{self.command[:80]}' error='{self.error[:120]}'"


@dataclass
class ResourceCheck:
    resource_type: str
    name: str
    exists: bool
    detail: str

    def __str__(self):
        icon = "OK" if self.exists else "MISSING"
        return f"[{icon}] {self.resource_type}:{self.name} — {self.detail}"


@dataclass
class ResolveResult:
    resolved: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)

    @property
    def ok(self):
        return len(self.failures) == 0


class EngineResolver:
    """Eagerly resolves scenario variables and validates resource existence."""

    def __init__(self, exec_ctx):
        self.exec_ctx = exec_ctx
        self._context = getattr(exec_ctx, "kubectl_context", None)
        self._profile = getattr(exec_ctx, "profile", None)
        self._account_id = getattr(exec_ctx, "account_id", "")
        self._region = getattr(exec_ctx, "region", AWS_REGION)
        self._namespace = getattr(exec_ctx, "namespace", NAMESPACE)

    # ── Variable Resolution ─────────────────────────────────────────────

    def resolve_variables(self, scenario) -> ResolveResult:
        """Resolve all variables upfront. Fail-fast on discovery errors."""
        resolved = self._globals(scenario)
        failures = []

        for key, val in scenario.get("variables", {}).items():
            if isinstance(val, str):
                resolved[key] = val
            elif isinstance(val, dict) and "discovery" in val:
                cmd = self._substitute(val["discovery"], resolved)
                cmd = self._inject_profile(cmd)
                ok, stdout, stderr = _run_cmd(cmd, timeout=30, context=self._context)
                if ok and stdout.strip():
                    resolved[key] = stdout.strip()
                else:
                    failures.append(VarFailure(
                        key=key,
                        command=cmd,
                        error=stderr or "empty output",
                    ))

        return ResolveResult(resolved=resolved, failures=failures)

    def _globals(self, scenario) -> dict:
        """Build global variable map."""
        return {
            "NAMESPACE": scenario.get("namespace") or self._namespace,
            "AWS_REGION": self._region,
            "PROJECT_NAME": _PROJECT_NAME,
            "AWS_ACCOUNT_ID": self._account_id,
        }

    def _substitute(self, text: str, resolved: dict) -> str:
        """Replace ${VAR} references with resolved values."""
        for key, val in resolved.items():
            text = text.replace(f"${{{key}}}", val)
        return text

    def _inject_profile(self, cmd: str) -> str:
        """Inject --profile flag for AWS CLI commands if needed."""
        if self._profile and "aws " in cmd and "--profile " not in cmd:
            cmd = cmd.replace("aws ", f"aws --profile {self._profile} ", 1)
        return cmd

    # ── Resource Validation ──────────────────────────────────────────────

    def validate_resources(self, scenario, resolved: dict) -> list[ResourceCheck]:
        """Validate every referenced resource exists before execution."""
        checks = []
        verification = scenario.get("verification", {})
        steps = verification.get("steps") or verification.get("checks") or []
        trigger_creates = self._infer_trigger_creates(scenario)

        for step in steps:
            if step.get("skip_validation"):
                continue

            pod = step.get("pod", "")
            if pod and pod in trigger_creates:
                continue

            step_type = step.get("type", "")
            check = self._validate_step_resource(step_type, step, resolved)
            if check:
                checks.append(check)

        return checks

    def _infer_trigger_creates(self, scenario) -> set[str]:
        """Detect resource names that trigger will create (kubectl apply/create)."""
        trigger = scenario.get("trigger", {})
        cmd = trigger.get("command", "")
        if not isinstance(cmd, str):
            return set()
        creates = set()
        if "apply -f" in cmd or "create" in cmd:
            import re
            for m in re.finditer(r'name:\s*(\S+)', cmd):
                creates.add(m.group(1))
        return creates

    def _validate_step_resource(self, step_type, step, resolved) -> ResourceCheck | None:
        """Dispatch resource validation by step type."""
        if step_type in ("cw_alarm", "alarm_state"):
            alarm = self._substitute(
                step.get("alarm") or step.get("alarm_name", ""), resolved)
            if alarm:
                return self._check_alarm(alarm)

        elif step_type in ("pod_status", "kubectl_check"):
            pod = step.get("pod", "")
            if pod:
                return self._check_deployment(pod, resolved)

        elif step_type == "lambda_logs":
            fn = step.get("function", "")
            if fn:
                fn = self._substitute(fn, resolved)
                return self._check_lambda(fn)

        elif step_type == "log_pattern":
            log_group = step.get("log_group", "")
            if log_group:
                log_group = self._substitute(log_group, resolved)
                return self._check_log_group(log_group)

        return None

    def _check_alarm(self, alarm_name: str) -> ResourceCheck:
        """Verify CloudWatch alarm exists."""
        import boto3
        try:
            session = boto3.Session(profile_name=self._profile) if self._profile else boto3.Session()
            client = session.client("cloudwatch", region_name=self._region)
            resp = client.describe_alarms(AlarmNames=[alarm_name])
            exists = len(resp.get("MetricAlarms", [])) > 0 or len(resp.get("CompositeAlarms", [])) > 0
            detail = f"state={resp['MetricAlarms'][0]['StateValue']}" if exists else "not found"
            return ResourceCheck("alarm", alarm_name, exists, detail)
        except Exception as e:
            return ResourceCheck("alarm", alarm_name, False, str(e)[:120])

    def _check_deployment(self, name: str, resolved: dict) -> ResourceCheck:
        """Verify Kubernetes deployment exists."""
        ns = resolved.get("NAMESPACE", self._namespace)
        cmd = f"kubectl get deployment/{name} -n {ns} --no-headers"
        ok, stdout, stderr = _run_cmd(cmd, timeout=10, context=self._context)
        if ok:
            return ResourceCheck("deployment", name, True, stdout[:80])
        return ResourceCheck("deployment", name, False, stderr[:120] or "not found")

    def _check_lambda(self, function_name: str) -> ResourceCheck:
        """Verify Lambda function exists."""
        import boto3
        try:
            session = boto3.Session(profile_name=self._profile) if self._profile else boto3.Session()
            client = session.client("lambda", region_name=self._region)
            client.get_function(FunctionName=function_name)
            return ResourceCheck("lambda", function_name, True, "exists")
        except Exception as e:
            err = str(e)
            exists = "ResourceNotFoundException" not in err
            return ResourceCheck("lambda", function_name, exists,
                                 "exists (access error)" if exists else "not found")

    def _check_log_group(self, log_group: str) -> ResourceCheck:
        """Verify CloudWatch log group exists."""
        import boto3
        try:
            session = boto3.Session(profile_name=self._profile) if self._profile else boto3.Session()
            client = session.client("logs", region_name=self._region)
            resp = client.describe_log_groups(logGroupNamePrefix=log_group, limit=1)
            groups = resp.get("logGroups", [])
            exists = any(g["logGroupName"] == log_group for g in groups)
            return ResourceCheck("log_group", log_group, exists,
                                 "exists" if exists else "not found")
        except Exception as e:
            return ResourceCheck("log_group", log_group, False, str(e)[:120])

    # ── Utility: apply resolved vars to entire scenario ──────────────────

    def apply_resolved(self, scenario: dict, resolved: dict) -> dict:
        """Substitute all ${VAR} in scenario commands/configs. Returns mutated scenario."""
        def _replace(obj):
            if isinstance(obj, str):
                for k, v in resolved.items():
                    obj = obj.replace(f"${{{k}}}", v)
                return obj
            elif isinstance(obj, dict):
                return {key: _replace(val) for key, val in obj.items()}
            elif isinstance(obj, list):
                return [_replace(item) for item in obj]
            return obj

        for key in ("trigger", "verification", "restore", "pre_cleanup", "effect_check"):
            if key in scenario:
                scenario[key] = _replace(scenario[key])
        return scenario

    # ── Diagnostic: check for unresolved variables ───────────────────────

    def find_unresolved(self, scenario: dict) -> list[str]:
        """Find any remaining ${VAR} references after resolution."""
        text = str(scenario.get("trigger", "")) + str(scenario.get("verification", ""))
        text += str(scenario.get("restore", "")) + str(scenario.get("pre_cleanup", ""))
        return re.findall(r'\$\{([A-Z_][A-Z0-9_]*)\}', text)
