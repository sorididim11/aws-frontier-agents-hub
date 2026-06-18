"""Simulation Engine v2 — Safety Policy.

인프라별 allow/deny 규칙을 선언적 데이터로 관리.
새 인프라 추가 시 규칙만 추가하면 됨 — 코드 수정 없음.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class InfraType(str, Enum):
    EKS = "eks"
    ECS = "ecs"
    EC2 = "ec2"
    LAMBDA = "lambda"
    RDS = "rds"
    FIS = "fis"
    DYNAMODB = "dynamodb"
    ELASTICACHE = "elasticache"
    ROUTE53 = "route53"


@dataclass(frozen=True)
class WriteRule:
    """단일 write 명령 허용 규칙."""
    prefix: str
    cleanup: str | None = None
    requires_restore: bool = True


# ─── 인프라별 Write 규칙 ───

EKS_WRITE_RULES = [
    WriteRule("kubectl scale", cleanup="kubectl scale {target} --replicas={original}"),
    WriteRule("kubectl delete pod", cleanup=None, requires_restore=False),
    WriteRule("kubectl set resources", cleanup="kubectl set resources {target} --limits=memory=128Mi --requests=memory=64Mi"),
    WriteRule("kubectl apply -f", cleanup="kubectl delete {kind} {name}"),
    WriteRule("kubectl rollout restart", cleanup=None, requires_restore=False),
    WriteRule("kubectl patch", cleanup=None),
]

FIS_WRITE_RULES = [
    WriteRule("aws fis start-experiment", cleanup="aws fis stop-experiment --id {experiment_id}"),
    WriteRule("aws fis create-experiment-template", cleanup="aws fis delete-experiment-template --id {template_id}"),
]

EC2_WRITE_RULES = [
    WriteRule("aws ec2 revoke-security-group-ingress", cleanup="aws ec2 authorize-security-group-ingress"),
    WriteRule("aws ec2 revoke-security-group-egress", cleanup="aws ec2 authorize-security-group-egress"),
    WriteRule("aws ec2 stop-instances", cleanup="aws ec2 start-instances --instance-ids {ids}"),
    WriteRule("aws ec2 create-network-acl-entry", cleanup="aws ec2 delete-network-acl-entry"),
]

RDS_WRITE_RULES = [
    WriteRule("aws rds failover-db-cluster", cleanup=None, requires_restore=False),
    WriteRule("aws rds reboot-db-instance", cleanup=None, requires_restore=False),
    WriteRule("aws rds modify-db-parameter-group", cleanup="aws rds modify-db-parameter-group"),
]

LAMBDA_WRITE_RULES = [
    WriteRule("aws lambda put-function-concurrency", cleanup="aws lambda delete-function-concurrency"),
    WriteRule("aws lambda update-function-configuration", cleanup="aws lambda update-function-configuration"),
]

DYNAMODB_WRITE_RULES = [
    WriteRule("aws dynamodb update-table", cleanup="aws dynamodb update-table"),
]

ECS_WRITE_RULES = [
    WriteRule("aws ecs stop-task", cleanup=None, requires_restore=False),
    WriteRule("aws ecs update-service", cleanup="aws ecs update-service"),
]

ELASTICACHE_WRITE_RULES = [
    WriteRule("aws elasticache reboot-cache-cluster", cleanup=None, requires_restore=False),
    WriteRule("aws elasticache modify-cache-cluster", cleanup="aws elasticache modify-cache-cluster"),
]

ROUTE53_WRITE_RULES = [
    WriteRule("aws route53 change-resource-record-sets", cleanup="aws route53 change-resource-record-sets"),
]

# ─── Read 규칙 (Generator + Verifier 공용) ───

READ_PREFIXES = [
    "kubectl get", "kubectl describe", "kubectl logs", "kubectl top",
    "kubectl explain", "kubectl auth", "kubectl api-resources",
    "aws cloudwatch describe", "aws cloudwatch get", "aws cloudwatch list",
    "aws logs describe", "aws logs get", "aws logs filter",
    "aws eks describe", "aws eks list",
    "aws sts get-caller-identity",
    "aws ec2 describe", "aws elbv2 describe",
    "aws fis list", "aws fis get",
    "aws rds describe",
    "aws lambda get", "aws lambda list",
    "aws ecs describe", "aws ecs list",
    "aws dynamodb describe", "aws dynamodb list",
    "aws elasticache describe",
    "aws route53 list", "aws route53 get",
    "aws autoscaling describe",
    "aws iam get", "aws iam list",
    "aws secretsmanager describe", "aws secretsmanager list",
    "aws ssm get", "aws ssm describe",
]

# ─── Probe 규칙 (Verifier 전용 — 비파괴적 확인) ───

PROBE_PREFIXES = [
    "curl ", "wget ", "dig ", "nslookup ", "nc -z",
    "ping -c", "traceroute ",
]

# ─── 항상 거부 ───

DENY_PATTERNS = [
    "rm -rf /", "mkfs", "> /dev/", "| bash", "dd if=",
    "chmod 777", "curl | sh", "wget -O - | sh",
    "aws iam delete", "aws organizations",
]

# ─── Write 규칙 레지스트리 ───

ALL_WRITE_RULES: dict[InfraType, list[WriteRule]] = {
    InfraType.EKS: EKS_WRITE_RULES,
    InfraType.FIS: FIS_WRITE_RULES,
    InfraType.EC2: EC2_WRITE_RULES,
    InfraType.RDS: RDS_WRITE_RULES,
    InfraType.LAMBDA: LAMBDA_WRITE_RULES,
    InfraType.DYNAMODB: DYNAMODB_WRITE_RULES,
    InfraType.ECS: ECS_WRITE_RULES,
    InfraType.ELASTICACHE: ELASTICACHE_WRITE_RULES,
    InfraType.ROUTE53: ROUTE53_WRITE_RULES,
}


@dataclass
class SafetyPolicy:
    """Per-run safety policy assembled from scenario infra types."""

    write_rules: list[WriteRule] = field(default_factory=list)
    read_prefixes: list[str] = field(default_factory=lambda: list(READ_PREFIXES))
    probe_prefixes: list[str] = field(default_factory=lambda: list(PROBE_PREFIXES))
    deny_patterns: list[str] = field(default_factory=lambda: list(DENY_PATTERNS))

    def validate_write(self, command: str) -> tuple[bool, str]:
        """Write 명령 검증. (allowed, reason) 반환."""
        if self._is_denied(command):
            return False, f"Denied pattern matched: {command[:80]}"
        for rule in self.write_rules:
            if command.strip().startswith(rule.prefix):
                return True, ""
        return False, f"No write rule matches: {command[:80]}"

    def validate_read(self, command: str) -> tuple[bool, str]:
        """Read 명령 검증."""
        if self._is_denied(command):
            return False, f"Denied pattern matched: {command[:80]}"
        for prefix in self.read_prefixes:
            if command.strip().startswith(prefix):
                return True, ""
        return False, f"No read rule matches: {command[:80]}"

    def validate_probe(self, command: str) -> tuple[bool, str]:
        """Probe 명령 검증."""
        if self._is_denied(command):
            return False, f"Denied pattern matched: {command[:80]}"
        for prefix in self.probe_prefixes:
            if command.strip().startswith(prefix):
                return True, ""
        return False, f"No probe rule matches: {command[:80]}"

    def get_cleanup_hint(self, command: str) -> str | None:
        """명령에 대한 cleanup 힌트 반환."""
        for rule in self.write_rules:
            if command.strip().startswith(rule.prefix):
                return rule.cleanup
        return None

    def _is_denied(self, command: str) -> bool:
        return any(p in command for p in self.deny_patterns)

    @classmethod
    def for_scenario(cls, scenario: dict) -> "SafetyPolicy":
        """시나리오에서 인프라 유형을 감지하고 적절한 policy 조립."""
        infra_types = _detect_infra_types(scenario)
        write_rules = []
        for itype in infra_types:
            write_rules.extend(ALL_WRITE_RULES.get(itype, []))
        return cls(write_rules=write_rules)

    @classmethod
    def allow_all_known(cls) -> "SafetyPolicy":
        """모든 등록된 인프라의 write 규칙을 포함하는 policy."""
        write_rules = []
        for rules in ALL_WRITE_RULES.values():
            write_rules.extend(rules)
        return cls(write_rules=write_rules)


def _detect_infra_types(scenario: dict) -> list[InfraType]:
    """시나리오의 trigger/restore 명령에서 인프라 유형 자동 감지."""
    types = set()
    commands = []
    if scenario.get("trigger", {}).get("command"):
        commands.append(scenario["trigger"]["command"])
    if scenario.get("restore", {}).get("command"):
        commands.append(scenario["restore"]["command"])
    if scenario.get("pre_cleanup", {}).get("command"):
        commands.append(scenario["pre_cleanup"]["command"])

    for cmd in commands:
        if "kubectl" in cmd:
            types.add(InfraType.EKS)
        if "aws fis" in cmd:
            types.add(InfraType.FIS)
        if "aws ec2" in cmd:
            types.add(InfraType.EC2)
        if "aws rds" in cmd:
            types.add(InfraType.RDS)
        if "aws lambda" in cmd:
            types.add(InfraType.LAMBDA)
        if "aws dynamodb" in cmd:
            types.add(InfraType.DYNAMODB)
        if "aws ecs" in cmd:
            types.add(InfraType.ECS)
        if "aws elasticache" in cmd:
            types.add(InfraType.ELASTICACHE)
        if "aws route53" in cmd:
            types.add(InfraType.ROUTE53)

    if not types:
        types.add(InfraType.EKS)

    return list(types)
