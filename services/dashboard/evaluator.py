"""
Rubric-based investigation evaluator using LLM-as-a-Judge pattern.
Each rubric criterion is evaluated independently to avoid bias.
"""
import json
import os
import boto3

from app_config import AWS_REGION

DEFAULT_MODEL = "us.anthropic.claude-opus-4-6-v1"


def evaluate_investigation(journal_messages, scenario, model_id=None):
    """Evaluate investigation quality using scenario rubric.
    Returns per-criterion scores + overall weighted score."""
    raw_rubric = scenario.get("evaluation_rubric", {})
    if not raw_rubric:
        return {"error": "시나리오에 evaluation_rubric 없음"}

    if isinstance(raw_rubric, list):
        criteria_list = raw_rubric
    elif isinstance(raw_rubric, dict) and "criteria" in raw_rubric:
        criteria_list = raw_rubric["criteria"]
    elif isinstance(raw_rubric, dict):
        criteria_list = [{"id": k, **v} for k, v in raw_rubric.items()]
    else:
        return {"error": f"evaluation_rubric 형식 미지원: {type(raw_rubric).__name__}"}

    model = model_id or DEFAULT_MODEL
    expected_rc = scenario.get("expected_root_cause", "")
    msgs_text = "\n".join([f"[{m.get('time','')}] {m.get('text','')}" for m in journal_messages])

    results = {}
    total_score = 0
    total_weight = 0

    for i, criterion in enumerate(criteria_list):
        criterion_id = criterion.get("id") or criterion.get("criterion") or f"c{i}"
        weight = criterion.get("weight", 10)
        criteria_text = criterion.get("criteria") or criterion.get("description") or criterion.get("criterion", "")
        required_sources = criterion.get("required", [])

        score, reasoning = _evaluate_criterion(
            criterion_id, criteria_text, required_sources,
            msgs_text, expected_rc, model
        )
        if score is None:
            results[criterion_id] = {
                "score": None, "weight": weight, "weighted_score": None,
                "reasoning": reasoning, "criteria": criteria_text, "error": True,
            }
            continue
        results[criterion_id] = {
            "score": score,
            "weight": weight,
            "weighted_score": round(score * weight / 10, 1),
            "reasoning": reasoning,
            "criteria": criteria_text,
        }
        total_score += score * weight / 10
        total_weight += weight

    overall = round(total_score / (total_weight / 10), 1) if total_weight > 0 else 0
    passing = raw_rubric.get("passing_score") if isinstance(raw_rubric, dict) else None

    return {
        "overall_score": overall,
        "max_score": 10,
        "passing_score": passing,
        "criteria_results": results,
        "model": model,
        "message_count": len(journal_messages),
    }


def _evaluate_criterion(criterion_id, criteria_text, required_sources, msgs_text, expected_rc, model):
    """Evaluate a single criterion independently. Returns (score 1-10, reasoning)."""
    prompt = f"""당신은 DevOps 조사 품질 평가 전문가입니다.

아래 조사 메시지를 읽고, 하나의 평가 기준에 대해서만 점수를 매기세요.

## 평가 기준
- ID: {criterion_id}
- 기준: {criteria_text}
- 예상 근본 원인: {expected_rc}
"""
    if required_sources:
        prompt += f"- 필수 데이터 소스: {', '.join(required_sources)}\n"

    prompt += f"""
## 조사 메시지
{msgs_text}

## 응답 형식 (JSON만, 다른 텍스트 없이)
{{"score": 1~10, "reasoning": "한국어 2-3줄 근거"}}
"""
    try:
        client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        resp = client.invoke_model(
            modelId=model,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 500,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }),
            contentType="application/json",
            accept="application/json",
        )
        raw = json.loads(resp["body"].read())["content"][0]["text"].strip()
        if raw.startswith("```"):
            import re
            raw = re.sub(r'^```json?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
        result = json.loads(raw)
        return result.get("score", 5), result.get("reasoning", "")
    except Exception as e:
        return None, f"평가 실패: {e}"


def auto_evaluate_run(run_id, task_id, scenario, agent_space_id):
    """자동 평가: journal fetch → evaluate → DDB 저장. Background thread에서 호출."""
    if not scenario.get("evaluation_rubric"):
        return None

    try:
        import boto3
        from app_config import _profile_for_space
        profile = _profile_for_space(agent_space_id) if agent_space_id else None
        session = boto3.Session(profile_name=profile, region_name=AWS_REGION) if profile else boto3.Session(region_name=AWS_REGION)
        client = session.client("devops-agent", region_name=AWS_REGION)

        exec_resp = client.list_executions(agentSpaceId=agent_space_id, taskId=task_id, limit=10)
        messages = []
        for exe in exec_resp.get("executions", []):
            jr = client.list_journal_records(
                agentSpaceId=agent_space_id, executionId=exe["executionId"],
                limit=100, order="ASC",
            )
            for r in jr.get("records", []):
                content = r.get("content", {})
                raw_text = content.get("text", "") if isinstance(content, dict) else str(content)
                messages.append({
                    "text": raw_text,
                    "time": str(r.get("createdAt", ""))[:19],
                    "record_type": r.get("recordType", ""),
                })

        if not messages:
            return None

        result = evaluate_investigation(messages, scenario)

        history = _fetch_evaluation_history(scenario.get("id", ""), session)
        if history:
            comparison = compare_with_history(result, history)
            if not comparison.get("consistent"):
                result["regression_warning"] = comparison

        from decimal import Decimal
        from app_config import _boto_session, RUNS_TABLE as _RUNS_TABLE
        ddb_session = _boto_session()
        tbl = ddb_session.resource("dynamodb", region_name=AWS_REGION).Table(_RUNS_TABLE)
        eval_item = json.loads(json.dumps(result), parse_float=Decimal)
        tbl.put_item(Item={
            "run_id": run_id, "record_type": "evaluation",
            "scenario_id": scenario.get("id", ""), "task_id": task_id,
            **eval_item,
        })
        tbl.put_item(Item={
            "run_id": run_id, "record_type": "journal",
            "task_id": task_id, "message_count": len(messages),
            "messages": messages[:50],
        })
        return result
    except Exception as e:
        print(f"[auto_evaluate_run] error: {e}")
        return None


def _fetch_evaluation_history(scenario_id, session=None):
    """DDB에서 동일 scenario_id의 과거 평가 결과 조회."""
    if not scenario_id:
        return []
    try:
        from boto3.dynamodb.conditions import Attr
        from app_config import _boto_session, RUNS_TABLE as _RUNS_TABLE
        ddb_session = _boto_session()
        tbl = ddb_session.resource("dynamodb", region_name=AWS_REGION).Table(_RUNS_TABLE)
        resp = tbl.scan(
            FilterExpression=Attr("record_type").eq("evaluation") & Attr("scenario_id").eq(scenario_id),
            Limit=10,
        )
        return resp.get("Items", [])
    except Exception:
        return []


def compare_with_history(current_eval, history_evals):
    """Compare current evaluation with historical evaluations for consistency check."""
    if not history_evals:
        return {"consistent": True, "message": "이전 평가 없음"}

    current_score = float(current_eval.get("overall_score", 0))
    hist_scores = [float(h.get("overall_score", 0)) for h in history_evals if h.get("overall_score")]

    if not hist_scores:
        return {"consistent": True, "message": "이전 점수 없음"}

    avg = sum(hist_scores) / len(hist_scores)
    diff = abs(current_score - avg)

    return {
        "consistent": diff < 2.0,
        "current_score": current_score,
        "historical_avg": round(avg, 1),
        "diff": round(diff, 1),
        "history_count": len(hist_scores),
        "message": f"현재 {current_score} vs 평균 {round(avg,1)} (차이 {round(diff,1)})"
    }
