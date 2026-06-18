"""Setup Wizard — 초기 설정 API + 페이지 라우트."""
import os
import yaml
import boto3
import requests as http_requests
from flask import Blueprint, jsonify, request, render_template, redirect

setup_bp = Blueprint("setup", __name__)

if os.environ.get("DEVOPS_AGENT_BUNDLED"):
    CONFIG_PATH = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "DevOpsAgent", "config.yaml")
else:
    CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


def is_configured() -> bool:
    """config.yaml에 유효한 aws.profile이 있는지 확인."""
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        return bool(cfg.get("aws", {}).get("profile"))
    except FileNotFoundError:
        return False


@setup_bp.route("/setup")
def setup_page():
    return render_template("setup.html")


@setup_bp.route("/api/setup/profiles")
def list_profiles():
    """로컬 ~/.aws에서 사용 가능한 프로파일 목록 반환. path 파라미터로 경로 지정 가능."""
    config_path = request.args.get("path", "").strip()
    env_override = {}
    if config_path:
        config_file = os.path.join(config_path, "config")
        creds_file = os.path.join(config_path, "credentials")
        if os.path.isfile(config_file):
            env_override["AWS_CONFIG_FILE"] = config_file
        if os.path.isfile(creds_file):
            env_override["AWS_SHARED_CREDENTIALS_FILE"] = creds_file

    orig_env = {}
    for k, v in env_override.items():
        orig_env[k] = os.environ.get(k)
        os.environ[k] = v

    try:
        profiles = boto3.Session().available_profiles or []
    finally:
        for k, orig in orig_env.items():
            if orig is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = orig

    return jsonify({"profiles": sorted(profiles), "path": config_path or "~/.aws"})


@setup_bp.route("/api/setup/validate-profile", methods=["POST"])
def validate_profile():
    """프로파일로 STS 호출하여 검증."""
    data = request.get_json(force=True)
    profile = data.get("profile", "")
    region = data.get("region", "us-east-1")

    try:
        session = boto3.Session(profile_name=profile, region_name=region)
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        return jsonify({
            "ok": True,
            "account_id": identity["Account"],
            "arn": identity["Arn"],
            "profile": profile,
            "region": region,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "profile": profile}), 400


@setup_bp.route("/api/setup/validate-credentials", methods=["POST"])
def validate_credentials():
    """Access Key/Secret으로 STS 호출하여 검증."""
    data = request.get_json(force=True)
    access_key = data.get("access_key", "")
    secret_key = data.get("secret_key", "")
    session_token = data.get("session_token", "")
    region = data.get("region", "us-east-1")

    try:
        kwargs = {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "region_name": region,
        }
        if session_token:
            kwargs["aws_session_token"] = session_token
        session = boto3.Session(**kwargs)
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        return jsonify({
            "ok": True,
            "account_id": identity["Account"],
            "arn": identity["Arn"],
            "region": region,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@setup_bp.route("/api/setup/discover", methods=["POST"])
def discover_infrastructure():
    """선택한 프로파일 또는 크레덴셜로 DynamoDB 테이블 발견 (prefix 필터)."""
    data = request.get_json(force=True)
    profile = data.get("profile", "")
    region = data.get("region", "us-east-1")
    access_key = data.get("access_key", "")
    secret_key = data.get("secret_key", "")
    session_token = data.get("session_token", "")
    prefix = data.get("prefix", "frontier-agent-hub")

    try:
        if access_key and secret_key:
            kwargs = {"aws_access_key_id": access_key, "aws_secret_access_key": secret_key, "region_name": region}
            if session_token:
                kwargs["aws_session_token"] = session_token
            session = boto3.Session(**kwargs)
        else:
            session = boto3.Session(profile_name=profile, region_name=region)

        # DynamoDB 테이블 (prefix 필터)
        tables = []
        try:
            ddb = session.client("dynamodb", region_name=region)
            all_tables = ddb.list_tables().get("TableNames", [])
            tables = [t for t in all_tables if t.startswith(prefix)]
        except Exception:
            pass

        return jsonify({"ok": True, "dynamodb_tables": tables, "prefix": prefix})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@setup_bp.route("/api/setup/detect-expert", methods=["POST"])
def detect_expert_cli():
    """Expert Agent용 CLI(claude, kiro-cli) 감지 — sidecar health에서 가져옴."""
    sidecar_url = os.environ.get("EXPERT_SIDECAR_URL", "http://localhost:3100")
    try:
        resp = http_requests.get(f"{sidecar_url}/health", timeout=5)
        data = resp.json()
        providers = {}
        for name in data.get("providers", []):
            label = "Claude Code" if name == "claude" else "Kiro CLI"
            providers[name] = {"path": name, "version": label}
        return jsonify({"ok": True, "providers": providers})
    except Exception:
        # fallback: 직접 감지 (로컬 실행 시)
        import shutil
        import subprocess
        results = {}
        for cmd, label in [("claude", "claude"), ("kiro-cli", "kiro")]:
            path = shutil.which(cmd)
            if path:
                try:
                    ver = subprocess.check_output(
                        [path, "--version"], timeout=5, text=True, stderr=subprocess.STDOUT
                    ).strip().split("\n")[0]
                    results[label] = {"path": path, "version": ver}
                except Exception:
                    results[label] = {"path": path, "version": "unknown"}
        return jsonify({"ok": True, "providers": results})


@setup_bp.route("/api/setup/save", methods=["POST"])
def save_config():
    """설정 저장 → config.yaml 업데이트 + DDB 테이블 자동 생성."""
    data = request.get_json(force=True)
    deploy_profile = data.get("deploy_profile", "")
    deploy_account_id = data.get("deploy_account_id", "")
    mgmt_profile = data.get("mgmt_profile", "")
    mgmt_account_id = data.get("mgmt_account_id", "")
    region = data.get("region", "us-east-1")
    prefix = data.get("prefix", "frontier-agent-hub")
    tables = data.get("tables", [])
    expert_providers = data.get("expert_providers", {})

    # 기존 config.yaml 읽기
    cfg = {}
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        pass

    # aws 섹션 업데이트
    cfg["aws"] = {
        "profile": deploy_profile,
        "region": region,
        "account_id": deploy_account_id,
    }
    if mgmt_profile:
        cfg["aws"]["mgmt_profile"] = mgmt_profile
        cfg["aws"]["mgmt_account_id"] = mgmt_account_id

    # 모든 프로파일 → account_id 매핑 저장 (Space Discovery에서 사용)
    profile_map = {}
    try:
        for p in boto3.Session().available_profiles:
            try:
                s = boto3.Session(profile_name=p, region_name=region)
                sts = s.client("sts")
                identity = sts.get_caller_identity()
                profile_map[identity["Account"]] = p
            except Exception:
                pass
    except Exception:
        pass
    if profile_map:
        cfg["aws"]["account_profiles"] = profile_map

    # DDB 테이블 설정
    default_tables = {
        "runs_table": f"{prefix}-scenario-runs",
        "events_table": f"{prefix}-investigation-events",
        "findings_table": f"{prefix}-security-findings",
    }
    if tables:
        # 기존 테이블 매칭
        for t in tables:
            if "scenario" in t or "runs" in t:
                default_tables["runs_table"] = t
            elif "event" in t or "investigation" in t:
                default_tables["events_table"] = t
            elif "finding" in t or "security" in t:
                default_tables["findings_table"] = t

    cfg["dynamodb"] = default_tables

    # Expert Agent CLI 설정
    if expert_providers:
        cfg["expert"] = {"providers": expert_providers}

    # EKS 클러스터 자동 발견 (각 계정별)
    clusters_cfg = {}
    for acct_id, prof in profile_map.items():
        try:
            s = boto3.Session(profile_name=prof, region_name=region)
            eks = s.client("eks", region_name=region)
            cluster_names = eks.list_clusters().get("clusters", [])
            for cn in cluster_names:
                ctx = f"arn:aws:eks:{region}:{acct_id}:cluster/{cn}"
                label = f"{cn}@{acct_id[-4:]}"
                clusters_cfg[label] = {
                    "account_id": acct_id,
                    "context": ctx,
                    "region": region,
                    "profile": prof,
                    "cluster_name": cn,
                }
        except Exception:
            pass
    if clusters_cfg:
        cfg["clusters"] = clusters_cfg

    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

    # DDB 테이블 자동 생성 (없으면) + ACTIVE 대기
    import time
    created = []
    try:
        session = boto3.Session(profile_name=deploy_profile, region_name=region)
        ddb = session.client("dynamodb", region_name=region)
        existing = ddb.list_tables().get("TableNames", [])

        for table_name in default_tables.values():
            if table_name not in existing:
                create_kwargs = {
                    "TableName": table_name,
                    "KeySchema": [
                        {"AttributeName": "run_id", "KeyType": "HASH"},
                        {"AttributeName": "record_type", "KeyType": "RANGE"},
                    ],
                    "AttributeDefinitions": [
                        {"AttributeName": "run_id", "AttributeType": "S"},
                        {"AttributeName": "record_type", "AttributeType": "S"},
                        {"AttributeName": "scenario_id", "AttributeType": "S"},
                    ],
                    "GlobalSecondaryIndexes": [
                        {
                            "IndexName": "scenario-id-index",
                            "KeySchema": [
                                {"AttributeName": "scenario_id", "KeyType": "HASH"},
                                {"AttributeName": "run_id", "KeyType": "RANGE"},
                            ],
                            "Projection": {"ProjectionType": "ALL"},
                        },
                    ],
                    "BillingMode": "PAY_PER_REQUEST",
                }
                ddb.create_table(**create_kwargs)
                created.append(table_name)

        # 생성된 테이블이 ACTIVE될 때까지 대기
        for table_name in created:
            for _ in range(30):
                resp = ddb.describe_table(TableName=table_name)
                if resp["Table"]["TableStatus"] == "ACTIVE":
                    break
                time.sleep(1)
    except Exception as e:
        return jsonify({"ok": True, "message": "설정 저장 완료 (DDB 생성 실패: " + str(e) + ")", "created_tables": []})

    # setup 완료 마커 (entrypoint.sh에서 재초기화 방지)
    open(os.path.join(os.path.dirname(__file__), ".setup_done"), "w").close()

    # 설정 반영: reload all module-level vars + reinit simulator
    from app_config import reload_cfg
    reload_cfg()

    try:
        from overview_app import _init_simulator
        _init_simulator()
    except Exception:
        pass

    if not os.environ.get("DEVOPS_AGENT_BUNDLED"):
        import threading
        import signal

        def _graceful_restart():
            try:
                master_pid = os.getppid()
                os.kill(master_pid, signal.SIGHUP)
            except Exception:
                pass

        threading.Timer(0.5, _graceful_restart).start()

    restart_required = bool(os.environ.get("DEVOPS_AGENT_BUNDLED"))
    return jsonify({"ok": True, "message": "설정이 저장되었습니다.", "created_tables": created, "restart_required": restart_required})
