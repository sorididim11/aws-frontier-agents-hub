"""
Integrations API routes (account-level services).

Includes:
  - /api/integrations — list available services (GitHub, Slack, GitLab, etc.)
  - /api/integrations/register — register GitLab or Splunk
  - /api/integrations/repos — list GitHub repos
  - /api/integrations/gitlab-repos — list GitLab projects
"""
import json

from flask import jsonify, request

from app_config import (
    AWS_REGION, RUNS_TABLE,
    _boto_session, _space_session,
)

from routes_space import space_bp


# ===================================================================
# Integrations API (account-level)
# ===================================================================

@space_bp.route("/api/integrations")
def api_integrations():
    """List available account-level services (GitHub, Slack, etc.) via list_services()."""
    try:
        account_id = request.args.get("account_id", "")
        session = _space_session(account_id=account_id) if account_id else _space_session()
        client = session.client("devops-agent")
        resp = client.list_services()
        integrations = []
        for svc in resp.get("services", []):
            svc_type = svc.get("serviceType", "")
            if svc_type.lower() in ("github", "slack", "gitlab", "mcpserver", "mcpserversplunk"):
                details = svc.get("additionalServiceDetails", {}).get(svc_type.lower(), {})
                entry = {
                    "integration_id": svc.get("serviceId", ""),
                    "service_id": svc.get("serviceId", ""),
                    "provider": svc_type.lower(),
                    "status": "ACTIVE",
                    "name": details.get("name", "") or svc.get("name", "") or svc_type,
                    "supports_private_connection": svc_type.lower() in ("github", "gitlab", "mcpserver"),
                    "private_connection_name": svc.get("privateConnectionName", ""),
                    "target_url": details.get("targetUrl", ""),
                }
                integrations.append(entry)
        return jsonify({"ok": True, "integrations": integrations})
    except Exception as e:
        return jsonify({"ok": False, "integrations": [], "error": str(e)})


@space_bp.route("/api/integrations/register", methods=["POST"])
def api_integrations_register():
    """Register a new GitLab or Splunk Cloud service integration.

    GitHub uses OAuth and must be registered via AWS Console.
    GitLab: register_service(service="gitlab", serviceDetails={gitlab: {targetUrl, tokenType, tokenValue}})
    Splunk: register_service(service="mcpserversplunk", serviceDetails={mcpserversplunk: {name, endpoint, authorizationConfig}})
    """
    data = request.json or {}
    provider = (data.get("provider") or "").lower()

    if provider == "github":
        return jsonify({"ok": False, "error": "GitHub은 OAuth 인증이 필요합니다. AWS 콘솔에서 등록하세요.",
                        "console_link": True})
    if provider not in ("gitlab", "mcpserversplunk"):
        return jsonify({"ok": False, "error": "지원되지 않는 provider (gitlab/mcpserversplunk만 인라인 등록 가능)"})

    try:
        account_id = data.get("account_id", "")
        session = _space_session(account_id=account_id) if account_id else _space_session()
        client = session.client("devops-agent")

        if provider == "gitlab":
            return _register_gitlab(client, data)
        else:
            return _register_splunk(client, data)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


def _register_gitlab(client, data):
    token = (data.get("token") or "").strip()
    host_url = (data.get("host_url") or "").strip()
    token_type = (data.get("token_type") or "personal").strip()
    group_id = (data.get("group_id") or "").strip()
    private_connection_name = (data.get("private_connection_name") or "").strip()

    if not token:
        return jsonify({"ok": False, "error": "Personal Access Token이 필요합니다"})
    if not host_url:
        return jsonify({"ok": False, "error": "GitLab 서버 URL이 필요합니다 (예: https://gitlab.example.com)"})

    gl_details = {
        "targetUrl": host_url,
        "tokenType": token_type,
        "tokenValue": token,
    }
    if group_id:
        gl_details["groupId"] = group_id

    kwargs = {
        "service": "gitlab",
        "serviceDetails": {"gitlab": gl_details},
    }
    if private_connection_name:
        kwargs["privateConnectionName"] = private_connection_name

    resp = client.register_service(**kwargs)
    service_id = resp.get("serviceId", "")
    return jsonify({
        "ok": True,
        "integration": {
            "integration_id": service_id,
            "provider": "gitlab",
            "status": "ACTIVE",
            "name": f"gitlab-{service_id[:8]}",
            "supports_private_connection": True,
            "private_connection_name": private_connection_name,
        },
    })


def _register_splunk(client, data):
    name = (data.get("name") or "").strip()
    endpoint = (data.get("endpoint") or "").strip()
    description = (data.get("description") or "").strip()
    auth_type = (data.get("auth_type") or "").strip()

    if not endpoint:
        return jsonify({"ok": False, "error": "Splunk 엔드포인트 URL이 필요합니다"})
    if not name:
        name = "splunk-cloud"
    if not auth_type:
        return jsonify({"ok": False, "error": "인증 방식을 선택하세요 (bearer_token/api_key/oauth_client)"})

    auth_config = {}
    if auth_type == "bearer_token":
        token_value = (data.get("token_value") or "").strip()
        if not token_value:
            return jsonify({"ok": False, "error": "Bearer Token 값이 필요합니다"})
        auth_config["bearerToken"] = {
            "tokenName": "splunk-token",
            "tokenValue": token_value,
            "authorizationHeader": "Bearer",
        }
    elif auth_type == "api_key":
        api_key_value = (data.get("api_key_value") or "").strip()
        api_key_header = (data.get("api_key_header") or "Authorization").strip()
        if not api_key_value:
            return jsonify({"ok": False, "error": "API Key 값이 필요합니다"})
        auth_config["apiKey"] = {
            "apiKeyName": "splunk-api-key",
            "apiKeyValue": api_key_value,
            "apiKeyHeader": api_key_header,
        }
    elif auth_type == "oauth_client":
        client_id = (data.get("client_id") or "").strip()
        client_secret = (data.get("client_secret") or "").strip()
        exchange_url = (data.get("exchange_url") or "").strip()
        if not client_id or not client_secret:
            return jsonify({"ok": False, "error": "Client ID와 Client Secret이 필요합니다"})
        if not exchange_url:
            return jsonify({"ok": False, "error": "Token Exchange URL이 필요합니다"})
        auth_config["oAuthClientCredentials"] = {
            "clientName": "splunk-oauth",
            "clientId": client_id,
            "clientSecret": client_secret,
            "exchangeUrl": exchange_url,
        }
    else:
        return jsonify({"ok": False, "error": f"지원되지 않는 인증 방식: {auth_type}"})

    splunk_details = {
        "name": name,
        "endpoint": endpoint,
        "authorizationConfig": auth_config,
    }
    if description:
        splunk_details["description"] = description

    resp = client.register_service(
        service="mcpserversplunk",
        serviceDetails={"mcpserversplunk": splunk_details},
    )
    service_id = resp.get("serviceId", "")
    return jsonify({
        "ok": True,
        "integration": {
            "integration_id": service_id,
            "provider": "mcpserversplunk",
            "status": "ACTIVE",
            "name": name,
            "supports_private_connection": False,
        },
    })


@space_bp.route("/api/integrations/repos")
def api_integration_repos():
    """List GitHub repos available via a specific GitHub service integration."""
    service_id = request.args.get("service_id", "")
    if not service_id:
        return jsonify({"ok": False, "repos": [], "error": "service_id 필수"})
    try:
        account_id = request.args.get("account_id", "")
        session = _space_session(account_id=account_id) if account_id else _space_session()
        client = session.client("devops-agent")
        repos = []
        seen = set()
        spaces_resp = client.list_agent_spaces()
        for sp in spaces_resp.get("agentSpaces", []):
            sid = sp.get("agentSpaceId", "")
            assocs = client.list_associations(agentSpaceId=sid).get("associations", [])
            for a in assocs:
                if a.get("serviceId") != service_id:
                    continue
                cfg = a.get("configuration", {}).get("github", {})
                owner = cfg.get("owner", "")
                repo_name = cfg.get("repoName", "")
                repo_id = cfg.get("repoId", "")
                key = f"{owner}/{repo_name}"
                if key and key not in seen:
                    seen.add(key)
                    repos.append({"owner": owner, "repo_name": repo_name, "repo_id": repo_id, "full_name": key})
        return jsonify({"ok": True, "repos": repos})
    except Exception as e:
        return jsonify({"ok": False, "repos": [], "error": str(e)})


@space_bp.route("/api/integrations/gitlab-repos")
def api_gitlab_repos():
    """List GitLab projects accessible via the registered GitLab PAT."""
    import ssl, urllib.request
    service_id = request.args.get("service_id", "")
    if not service_id:
        return jsonify({"ok": False, "repos": [], "error": "service_id 필수"})
    try:
        session = _boto_session()
        tbl = session.resource("dynamodb").Table(RUNS_TABLE)
        cred_resp = tbl.get_item(Key={"run_id": f"service-cred-{service_id}", "record_type": "service_credential"})
        cred = cred_resp.get("Item", {})
        token_value = cred.get("token_value", "")
        target_url = cred.get("target_url", "").rstrip("/")

        if not target_url:
            client = session.client("devops-agent")
            svc_resp = client.get_service(serviceId=service_id)
            svc_cfg = svc_resp.get("service", {}).get("additionalServiceDetails", {}).get("gitlab", {})
            target_url = svc_cfg.get("targetUrl", "").rstrip("/")

        if not target_url or not token_value:
            return jsonify({"ok": False, "repos": [], "error": "GitLab 토큰 정보를 찾을 수 없습니다. 서비스를 다시 등록하세요."})

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(
            f"{target_url}/api/v4/projects?membership=true&per_page=50&order_by=last_activity_at",
            headers={"PRIVATE-TOKEN": token_value},
        )
        with urllib.request.urlopen(req, timeout=20, context=ssl_ctx) as resp:
            projects = json.loads(resp.read())

        repos = []
        for p in projects:
            repos.append({
                "id": p.get("id"),
                "path_with_namespace": p.get("path_with_namespace", ""),
                "description": p.get("description", "") or "",
                "default_branch": p.get("default_branch", "main"),
            })
        return jsonify({"ok": True, "repos": repos})
    except Exception as e:
        return jsonify({"ok": False, "repos": [], "error": str(e)})
