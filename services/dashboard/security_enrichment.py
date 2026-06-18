"""
Security Enrichment — 컨텍스트 기반 위험 재평가 엔진.

Security Agent finding에 DevOps 운영 컨텍스트(토폴로지, 네트워크 정책,
모니터링 상태, 트래픽)를 결합하여 실제 운영 환경에서의 위험도를 재평가.

사용:
    from security_enrichment import SecurityEnrichment
    enricher = SecurityEnrichment()
    enriched = enricher.enrich_finding(finding)
"""
import json
import re
import subprocess
import shutil
from typing import Optional

from topology_provider import topology, ServiceLocation

KUBECTL = shutil.which("kubectl") or "/opt/homebrew/bin/kubectl"


class SecurityEnrichment:
    """Finding + DevOps 운영 컨텍스트 → adjusted_risk 계산."""

    RISK_SCORES = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}
    SCORE_TO_LEVEL = {4: "CRITICAL", 3: "HIGH", 2: "MEDIUM", 1: "LOW", 0: "INFO"}

    def enrich_finding(self, finding: dict) -> dict:
        """Finding에 운영 컨텍스트를 추가하여 위험도 재평가."""
        endpoint = finding.get("endpoint", "") or ""
        service_name = self._extract_service(endpoint)
        loc = topology.resolve(service_name) if service_name else None

        context = {
            "service_name": service_name,
            "resolved": loc is not None,
            "exposure": "unknown",
            "network_policy": "unknown",
            "monitoring": {"alarm_count": 0},
            "traffic_level": "unknown",
        }

        if loc:
            context["account_id"] = loc.account_id
            context["namespace"] = loc.namespace
            context["cluster"] = loc.cluster_label or loc.context
            context["exposure"] = self._check_exposure(loc)
            context["network_policy"] = self._check_network_policy(loc)
            context["monitoring"] = self._check_monitoring(loc, service_name)

        original_risk = finding.get("riskLevel", "MEDIUM")
        adjusted_risk = self._compute_adjusted_risk(original_risk, context)
        reason = self._explain_adjustment(original_risk, adjusted_risk, context)

        return {
            **finding,
            "operational_context": context,
            "adjusted_risk": adjusted_risk,
            "risk_changed": adjusted_risk != original_risk,
            "adjustment_reason": reason,
        }

    def enrich_findings(self, findings: list[dict]) -> list[dict]:
        """여러 findings를 일괄 재평가."""
        return [self.enrich_finding(f) for f in findings]

    def _compute_adjusted_risk(self, original_risk: str, context: dict) -> str:
        score = self.RISK_SCORES.get(original_risk, 2)

        if context["exposure"] == "internal_only":
            score -= 1
        if context["network_policy"] == "deny-default":
            score -= 0.5
        if context["monitoring"].get("alarm_count", 0) > 0:
            score -= 0.5

        clamped = max(0, min(4, round(score)))
        return self.SCORE_TO_LEVEL.get(clamped, "LOW")

    def _explain_adjustment(self, original: str, adjusted: str, context: dict) -> str:
        if original == adjusted:
            return "변경 없음"

        reasons = []
        if context["exposure"] == "internal_only":
            reasons.append("내부 전용 서비스 (외부 노출 없음)")
        if context["network_policy"] == "deny-default":
            reasons.append("NetworkPolicy deny-default 적용")
        if context["monitoring"].get("alarm_count", 0) > 0:
            reasons.append(f"CloudWatch 알람 {context['monitoring']['alarm_count']}개 (탐지 가능)")

        return f"{original} → {adjusted}: " + ", ".join(reasons) if reasons else f"{original} → {adjusted}"

    def _extract_service(self, endpoint: str) -> str:
        """엔드포인트 URL에서 서비스명 추출."""
        if not endpoint:
            return ""
        match = re.search(r'https?://([^/:]+)', endpoint)
        if not match:
            return ""
        host = match.group(1)
        parts = host.split(".")
        if parts:
            return parts[0]
        return ""

    def _check_exposure(self, loc: ServiceLocation) -> str:
        """서비스의 외부 노출 여부 확인 (Service type + Ingress)."""
        ctx_flag = f"--context {loc.context} " if loc.context else ""
        cmd = f"{KUBECTL} {ctx_flag}-n {loc.namespace} get svc {loc.service_name} -o jsonpath='{{.spec.type}}'"
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=10
            )
            svc_type = result.stdout.strip().strip("'")
            if svc_type == "LoadBalancer":
                return "external"
            if svc_type in ("ClusterIP", ""):
                ingress_cmd = (
                    f"{KUBECTL} {ctx_flag}-n {loc.namespace} get ingress "
                    f"-o jsonpath='{{.items[*].spec.rules[*].http.paths[*].backend.service.name}}'"
                )
                result2 = subprocess.run(
                    ingress_cmd, shell=True, capture_output=True, text=True, timeout=10
                )
                if loc.service_name in result2.stdout:
                    return "external"
                return "internal_only"
            return "internal_only"
        except Exception:
            return "unknown"

    def _check_network_policy(self, loc: ServiceLocation) -> str:
        """NetworkPolicy 적용 상태 확인."""
        ctx_flag = f"--context {loc.context} " if loc.context else ""
        cmd = (
            f"{KUBECTL} {ctx_flag}-n {loc.namespace} get networkpolicy "
            f"-o jsonpath='{{.items[*].metadata.name}}'"
        )
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=10
            )
            policies = result.stdout.strip()
            if not policies:
                return "none"
            if "deny" in policies.lower() or "default" in policies.lower():
                return "deny-default"
            return "custom"
        except Exception:
            return "unknown"

    def _check_monitoring(self, loc: ServiceLocation, service_name: str) -> dict:
        """CloudWatch 알람 존재 여부 확인."""
        try:
            cmd = (
                f"aws cloudwatch describe-alarms "
                f"--alarm-name-prefix '{service_name}' "
                f"--query 'length(MetricAlarms)' --output text"
            )
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=10
            )
            count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
            return {"alarm_count": count}
        except Exception:
            return {"alarm_count": 0}


# Module-level singleton
enrichment = SecurityEnrichment()
