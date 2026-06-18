"""
Shared configuration, AWS session helpers, and request utilities.
Extracted from overview_app.py for modularity.
"""
import ast
import json
import os
import time

from flask import request

import prompts as _prompts  # noqa: F401


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_cfg():
    """Load config.yaml from same directory (try yaml, fallback to simple parser)."""
    p = os.path.join(os.path.dirname(__file__), "config.yaml")
    if not os.path.exists(p):
        return {}
    try:
        import yaml
        with open(p) as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        pass
    result = {}
    with open(p) as f:
        stack = [result]
        indent_stack = [-1]
        for line in f:
            stripped = line.rstrip()
            if not stripped or stripped.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            content = stripped.lstrip()
            while indent <= indent_stack[-1] and len(stack) > 1:
                stack.pop()
                indent_stack.pop()
            if content.endswith(":"):
                key = content[:-1].strip()
                child = {}
                stack[-1][key] = child
                stack.append(child)
                indent_stack.append(indent)
            elif ":" in content:
                key, val = content.split(":", 1)
                val = val.strip().strip('"').strip("'")
                stack[-1][key.strip()] = val
    return result


def _cfg_get(cfg, path, default=None):
    val = cfg
    for k in path.split("."):
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
    return val if val is not None else default


_CFG = _load_cfg()
AWS_REGION = _cfg_get(_CFG, "aws.region", os.environ.get("AWS_REGION", "us-east-1"))
AGENT_SPACE_ID = _cfg_get(_CFG, "agent.space_id", os.environ.get("AGENT_SPACE_ID", ""))
RUNS_TABLE = _cfg_get(_CFG, "dynamodb.runs_table", os.environ.get("RUNS_TABLE", ""))
AWS_PROFILE = _cfg_get(_CFG, "aws.profile", os.environ.get("AWS_PROFILE", ""))


def reload_cfg():
    """config.yaml 재로드 후 모듈 레벨 변수 갱신."""
    global _CFG, AWS_REGION, AGENT_SPACE_ID, RUNS_TABLE, AWS_PROFILE
    _CFG = _load_cfg()
    AWS_REGION = _cfg_get(_CFG, "aws.region", os.environ.get("AWS_REGION", "us-east-1"))
    AGENT_SPACE_ID = _cfg_get(_CFG, "agent.space_id", os.environ.get("AGENT_SPACE_ID", ""))
    RUNS_TABLE = _cfg_get(_CFG, "dynamodb.runs_table", os.environ.get("RUNS_TABLE", ""))
    AWS_PROFILE = _cfg_get(_CFG, "aws.profile", os.environ.get("AWS_PROFILE", ""))

_SYSTEM_TAG_PREFIXES = ("aws:", "auto-delete", "CreatedBy")
_space_tag_cache = {}


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------

def _req_space_id(src="args"):
    """request에서 space_id를 안전하게 추출. 빈 문자열이면 AGENT_SPACE_ID로 fallback."""
    if src == "json":
        raw = (request.json or {}).get("space_id", "") if request.json else ""
    else:
        raw = request.args.get("space_id", "")
    return raw.strip() if raw and raw.strip() else AGENT_SPACE_ID


def _agent_space_id():
    """devops-agent API용 space_id. sp- prefix는 UI 식별자이므로 AGENT_SPACE_ID로 변환."""
    raw = request.args.get("space_id", "")
    if not raw or raw.startswith("sp-"):
        return AGENT_SPACE_ID
    return raw.strip()


# ---------------------------------------------------------------------------
# AWS session helpers
# ---------------------------------------------------------------------------

def _boto_session():
    """App 인프라(DDB 등) 접근용 기본 세션."""
    import boto3
    try:
        return boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    except Exception as e:
        print(f"[WARN] Profile '{AWS_PROFILE}' session failed, using default: {e}", flush=True)
        return boto3.Session(region_name=AWS_REGION)


def _space_session(space_id=None, account_id=None):
    """Space API(devops-agent) 호출용 세션. account_resolver 통해 resolve."""
    import boto3
    from account_resolver import resolver

    # 1. account_id → profile
    if account_id:
        from account_registry import registry
        profile = registry.get_profile(account_id)
        if profile:
            try:
                return boto3.Session(profile_name=profile, region_name=AWS_REGION)
            except Exception as e:
                print(f"[WARN] _space_session profile '{profile}' failed for account {account_id}: {e}", flush=True)

    # 2. space_id → account_resolver로 monitor account 조회
    if space_id:
        monitor = resolver.get_monitor_account(space_id)
        if monitor and monitor.profile:
            try:
                return boto3.Session(profile_name=monitor.profile, region_name=AWS_REGION)
            except Exception as e:
                print(f"[WARN] _space_session monitor profile '{monitor.profile}' failed: {e}", flush=True)

    print(f"[WARN] _space_session falling back to default session (space={space_id}, account={account_id})", flush=True)
    return _boto_session()


_session_cache = {}


def _assumed_session(role_arn, region=None):
    """STS AssumeRole로 cross-account 세션 생성."""
    import boto3
    primary = _boto_session()
    sts = primary.client("sts")
    account_id = role_arn.split(":")[4] if len(role_arn.split(":")) > 4 else "unknown"
    resp = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"devops-dashboard-{account_id}",
        DurationSeconds=3600,
    )
    creds = resp["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region or AWS_REGION,
    )


def _get_or_create_session(role_arn, region=None):
    """캐시된 세션 반환. 만료 5분 전이면 갱신."""
    now = time.time()
    if role_arn in _session_cache:
        session, expiry = _session_cache[role_arn]
        if now < expiry - 300:
            return session
    session = _assumed_session(role_arn, region)
    _session_cache[role_arn] = (session, now + 3600)
    return session


def _session_for_association(assoc):
    """Association에 맞는 세션. account_id→profile 우선, fallback=기본 세션."""
    account_id = assoc.get("account_id", "")
    if account_id:
        session = _session_for_account_id(account_id)
        if session:
            return session
    role_arn = assoc.get("role_arn", "")
    if not role_arn or not role_arn.startswith("arn:aws:iam::"):
        return _boto_session()
    try:
        return _get_or_create_session(role_arn)
    except Exception as e:
        raise RuntimeError(f"AssumeRole failed for {role_arn}: {e}") from e


def _session_for_space(space_meta):
    """Space 메타에서 세션 반환. profile 우선, 없으면 role_arn AssumeRole."""
    import boto3
    if not space_meta:
        return _boto_session()
    profile = space_meta.get("profile", "")
    if profile:
        try:
            return boto3.Session(profile_name=profile, region_name=AWS_REGION)
        except Exception as e:
            raise RuntimeError(f"Space profile '{profile}' session failed: {e}") from e
    aws_config = space_meta.get("aws_config", {})
    role_arn = (aws_config.get("aws", {}).get("role_arn")
                or space_meta.get("role_arn", ""))
    if not role_arn or not role_arn.startswith("arn:aws:iam::"):
        return _boto_session()
    try:
        return _get_or_create_session(role_arn)
    except Exception as e:
        raise RuntimeError(f"_session_for_space AssumeRole failed for {role_arn}: {e}") from e


def _session_for_account_id(account_id):
    """계정 ID로 세션 반환. profile 우선, 없으면 role_arn AssumeRole."""
    import boto3
    if not account_id:
        return _boto_session()
    try:
        from account_registry import registry
        acct = registry.get(account_id)
        if acct:
            if acct.profile:
                return boto3.Session(profile_name=acct.profile, region_name=AWS_REGION)
            if acct.role_arn and acct.role_arn.startswith("arn:aws:iam::"):
                return _get_or_create_session(acct.role_arn)
    except Exception as e:
        raise RuntimeError(f"_session_for_account_id failed ({account_id}): {e}") from e
    return _boto_session()


def _get_aws_associations(space_id, session=None):
    """AWS 타입 association 목록 반환. session 미지정 시 Space 소유 계정 세션 사용."""
    if session is None:
        profile = _profile_for_space(space_id)
        import boto3
        session = boto3.Session(profile_name=profile, region_name=AWS_REGION) if profile else _boto_session()
    client = session.client("devops-agent")
    aws_assocs = []
    try:
        assoc_resp = client.list_associations(agentSpaceId=space_id)
        for a in assoc_resp.get("associations", []):
            raw_cfg = a.get("configuration", a.get("serviceConfiguration", {}))
            if isinstance(raw_cfg, str):
                try:
                    raw_cfg = ast.literal_eval(raw_cfg)
                except Exception:
                    try:
                        raw_cfg = json.loads(raw_cfg)
                    except Exception:
                        raw_cfg = {}
            aws_cfg = raw_cfg.get("aws") or raw_cfg.get("sourceAws")
            if aws_cfg:
                aws_assocs.append({
                    "account_id": aws_cfg.get("accountId", ""),
                    "account_type": aws_cfg.get("accountType", ""),
                    "role_arn": aws_cfg.get("assumableRoleArn", ""),
                })
    except Exception as e:
        print(f"_get_aws_associations error: {e}", flush=True)
    return aws_assocs


def _tag_key_for_space(space_id):
    """리소스 스코프 태그 키 결정. DDB space_metadata > config > Space API 태그 순."""
    if space_id in _space_tag_cache:
        return _space_tag_cache[space_id]
    configured = _cfg_get(_CFG, "agent.boundary_tag_key")
    if configured:
        _space_tag_cache[space_id] = configured
        return configured
    # DDB space_metadata에서 app_tag_key 조회 (account_resolver 경유 불필요)
    try:
        session = _boto_session()
        tbl = session.resource("dynamodb").Table(RUNS_TABLE)
        resp = tbl.get_item(Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"})
        item = resp.get("Item", {})
        ddb_key = item.get("app_tag_key", "")
        if ddb_key:
            _space_tag_cache[space_id] = ddb_key
            return ddb_key
    except Exception:
        pass
    # Fallback: devops-agent API에서 Space 태그 조회 (account_resolver로 account_id resolve)
    try:
        from account_resolver import resolver
        mon = resolver.get_monitor_account(space_id)
        account_id = mon.account_id if mon else ""
        profile = mon.profile if mon else ""
        import boto3
        sess = boto3.Session(profile_name=profile, region_name=AWS_REGION) if profile else _boto_session()
        client = sess.client("devops-agent")
        arn = f"arn:aws:aidevops:{AWS_REGION}:{account_id}:agentspace/{space_id}"
        resp = client.list_tags_for_resource(resourceArn=arn)
        tags = resp.get("tags", {})
        for k, v in tags.items():
            k_stripped = k.strip()
            if not any(k_stripped.startswith(p) for p in _SYSTEM_TAG_PREFIXES):
                _space_tag_cache[space_id] = k_stripped
                return k_stripped
    except Exception as e:
        print(f"[TAG] Space 태그 조회 실패 ({space_id}): {e}", flush=True)
    _space_tag_cache[space_id] = None
    return None


_space_tag_value_cache = {}


def _tag_value_for_space(space_id):
    """Space의 앱 태그 값. DDB meta의 app_tag_value 반환."""
    if space_id in _space_tag_value_cache:
        return _space_tag_value_cache[space_id]
    try:
        session = _boto_session()
        tbl = session.resource("dynamodb").Table(RUNS_TABLE)
        resp = tbl.get_item(Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"})
        item = resp.get("Item", {})
        val = item.get("app_tag_value", "")
        _space_tag_value_cache[space_id] = val
        return val
    except Exception:
        pass
    _space_tag_value_cache[space_id] = None
    return None


# ---------------------------------------------------------------------------
# Space → Profile resolution
# ---------------------------------------------------------------------------

def _profile_for_space(space_id):
    """Space 소유 계정의 AWS 프로필 반환. account_resolver에 위임."""
    if not space_id:
        return AWS_PROFILE
    from account_resolver import resolver
    profile = resolver.get_monitor_profile(space_id)
    return profile or AWS_PROFILE


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

AVAILABLE_MODELS = {
    "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "sonnet": "us.anthropic.claude-sonnet-4-6",
    "opus": "us.anthropic.claude-opus-4-6-v1",
}


def _fetch_tagged_resources(tag_key, tag_value=None, session=None):
    """tag_key (+ optional tag_value)로 리소스 검색. tag_value=None이면 키 존재만으로 매칭."""
    if session is None:
        session = _boto_session()
    client = session.client("resourcegroupstaggingapi")
    all_res = []
    token = ""
    while True:
        tag_filter = {"Key": tag_key}
        if tag_value:
            tag_filter["Values"] = [tag_value]
        kwargs = {
            "TagFilters": [tag_filter],
            "ResourcesPerPage": 100,
        }
        if token:
            kwargs["PaginationToken"] = token
        resp = client.get_resources(**kwargs)
        all_res.extend(resp.get("ResourceTagMappingList", []))
        token = resp.get("PaginationToken", "")
        if not token:
            break
    by_service = {}
    for r in all_res:
        arn = r.get("ResourceARN", "")
        parts = arn.split(":")
        svc = parts[2] if len(parts) > 2 else "unknown"
        if svc not in by_service:
            by_service[svc] = []
        name = arn.split("/")[-1] if "/" in arn else arn.split(":")[-1]
        # Extract App tag value from resource tags
        app_val = ""
        for t in r.get("Tags", []):
            if t.get("Key") == tag_key:
                app_val = t.get("Value", "")
                break
        by_service[svc].append({"arn": arn, "name": name, "app": app_val})
    return len(all_res), by_service
