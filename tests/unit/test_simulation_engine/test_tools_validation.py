"""Unit tests for L1-L3 validation logic (submit_scenario path).

tools.py가 strands를 import하므로, validation 로직(_run_l1_l3_validation)은
직접 테스트하지 않고 mock 기반으로 검증한다.
"""

import sys
import os
import json
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "services", "dashboard"))

# strands import를 피하기 위해 tools 모듈 mock
sys.modules["strands"] = MagicMock()
sys.modules["strands.tools"] = MagicMock()
sys.modules["strands.agent"] = MagicMock()
sys.modules["strands.agent.agent"] = MagicMock()
sys.modules["strands.models"] = MagicMock()
sys.modules["strands.models.bedrock"] = MagicMock()


def test_inject_kubectl_context():
    from simulation_engine.tools import _inject_kubectl_context, AgentContext

    ctx = AgentContext(kubectl_context="prod-cluster")
    result = _inject_kubectl_context("kubectl get pods -n coins", ctx)
    assert "--context prod-cluster" in result
    assert result.startswith("kubectl --context prod-cluster get pods")


def test_inject_kubectl_context_no_override():
    from simulation_engine.tools import _inject_kubectl_context, AgentContext

    ctx = AgentContext(kubectl_context="prod")
    cmd = "kubectl --context custom get pods"
    result = _inject_kubectl_context(cmd, ctx)
    assert result == cmd  # no double injection


def test_inject_kubectl_context_empty():
    from simulation_engine.tools import _inject_kubectl_context, AgentContext

    ctx = AgentContext(kubectl_context="")
    result = _inject_kubectl_context("kubectl get pods", ctx)
    assert result == "kubectl get pods"


def test_inject_aws_context():
    from simulation_engine.tools import _inject_aws_context, AgentContext

    ctx = AgentContext(profile="member1-acc", region="us-west-2")
    result = _inject_aws_context("aws cloudwatch describe-alarms", ctx)
    assert "--profile member1-acc" in result
    assert "--region us-west-2" in result


def test_inject_aws_context_no_override():
    from simulation_engine.tools import _inject_aws_context, AgentContext

    ctx = AgentContext(profile="member1-acc", region="us-east-1")
    cmd = "aws cloudwatch describe-alarms --profile custom --region ap-northeast-2"
    result = _inject_aws_context(cmd, ctx)
    assert result == cmd


def test_agent_context_defaults():
    from simulation_engine.tools import AgentContext

    ctx = AgentContext()
    assert ctx.kubectl_context == ""
    assert ctx.profile == ""
    assert ctx.region == "us-east-1"
    assert ctx.namespace == "default"


def test_agent_context_custom():
    from simulation_engine.tools import AgentContext

    ctx = AgentContext(kubectl_context="dev", profile="dev-acc", region="ap-northeast-2", namespace="dockercoins")
    assert ctx.kubectl_context == "dev"
    assert ctx.namespace == "dockercoins"
