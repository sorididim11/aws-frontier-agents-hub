"""
Accounts API routes for Space creation multi-account flow.

Includes:
  - /api/accounts — list registered accounts
  - /api/accounts/<id>/clusters — list EKS clusters in account
  - Helper functions: _account_id, _pentest_service_role, _build_vpc_config,
    _create_security_agent_space, _get_github_repo_id, _provision_agent_role
"""
import json

from flask import jsonify, request

from app_config import (
    _CFG, _cfg_get, AWS_REGION, RUNS_TABLE,
    _boto_session, _space_session, _session_for_account_id,
)

from routes_space import space_bp


# ===================================================================
# Accounts API (for Space creation multi-account flow)
# ===================================================================

@space_bp.route("/api/accounts")
def api_accounts():
    """List registered accounts for Space creation dropdowns.

    Sources (merged, deduplicated):
      1. AccountRegistry (clusters + env files + agent space associations)
      2. config.yaml aws.account_id (deploy account, always primary)
      3. config.yaml aws.account_profiles (all known accounts)
    """
    try:
        from account_registry import registry
        accounts = registry.list_all()
        seen = set()
        result = []
        for acct in accounts:
            if acct.account_id and acct.account_id not in seen:
                seen.add(acct.account_id)
                result.append({
                    "account_id": acct.account_id,
                    "profile": acct.profile,
                    "region": acct.region,
                })

        # Space context가 있으면 역할(monitor/source) 추가
        space_id = request.args.get("space_id", "")
        if space_id:
            from account_resolver import resolver
            space_accounts = resolver.get_space_accounts(space_id)
            role_map = {sa.account_id: sa.role for sa in space_accounts}
            for r in result:
                r["account_type"] = role_map.get(r["account_id"], "")
        else:
            for r in result:
                r["account_type"] = ""

        return jsonify({"ok": True, "accounts": result})
    except Exception as e:
        return jsonify({"ok": False, "accounts": [], "error": str(e)})


@space_bp.route("/api/accounts/<account_id>/clusters")
def api_account_clusters(account_id):
    """List EKS clusters in a specific account."""
    try:
        import boto3
        from account_registry import registry
        region = request.args.get("region", "") or AWS_REGION
        profile = registry.get_profile(account_id) or ""
        session = boto3.Session(profile_name=profile, region_name=region) if profile else _boto_session()
        eks = session.client("eks", region_name=region)
        resp = eks.list_clusters()
        clusters = []
        for name in resp.get("clusters", []):
            clusters.append({"name": name, "region": region})
        return jsonify({"ok": True, "clusters": clusters})
    except Exception as e:
        return jsonify({"ok": False, "clusters": [], "error": str(e)})


def _account_id(session):
    """Get current account ID."""
    return session.client("sts").get_caller_identity()["Account"]


_PENTEST_ROLE_CACHE = None

def _pentest_service_role():
    """Get service role for pentest from existing pentests."""
    global _PENTEST_ROLE_CACHE
    if _PENTEST_ROLE_CACHE:
        return _PENTEST_ROLE_CACHE
    role = _cfg_get(_CFG, "security_agent.pentest_service_role", "")
    if role:
        _PENTEST_ROLE_CACHE = role
        return role
    try:
        session = _space_session()
        sa = session.client("securityagent")
        spaces = sa.list_agent_spaces().get("agentSpaceSummaries", [])
        for sp in spaces:
            sid = sp["agentSpaceId"]
            pentests = sa.list_pentests(agentSpaceId=sid).get("pentestSummaries", [])
            if pentests:
                detail = sa.batch_get_pentests(agentSpaceId=sid, pentestIds=[pentests[0]["pentestId"]])
                for p in detail.get("pentests", []):
                    if p.get("serviceRole"):
                        _PENTEST_ROLE_CACHE = p["serviceRole"]
                        return _PENTEST_ROLE_CACHE
    except Exception:
        pass
    return ""


def _build_vpc_config(data):
    """Build vpcConfig dict for create_pentest from form data."""
    account_id = data.get("primary_account_id", "")
    region = AWS_REGION
    vpc_id = data.get("pentest_vpc_id", "")
    sg_id = data.get("pentest_sg_id", "")
    subnet_ids = data.get("pentest_subnet_ids", [])
    if not subnet_ids:
        single = data.get("pentest_subnet_id", "")
        if single:
            subnet_ids = [single]
    if not (vpc_id and subnet_ids and sg_id):
        return None
    return {
        "vpcArn": f"arn:aws:ec2:{region}:{account_id}:vpc/{vpc_id}",
        "subnetArns": [f"arn:aws:ec2:{region}:{account_id}:subnet/{s}" for s in subnet_ids],
        "securityGroupArns": [f"arn:aws:ec2:{region}:{account_id}:security-group/{sg_id}"],
    }


def _create_security_agent_space(session, name, description, github_repo, pentest_url="", vpc_config=None, zone_id=""):
    """Create a Security Agent Space, connect GitHub repo, and optionally create a Pentest."""
    sec_client = session.client("securityagent")

    sec_name = f"{name}-security"

    existing = sec_client.list_agent_spaces().get("agentSpaceSummaries", [])
    if any(s.get("name") == sec_name for s in existing):
        raise Exception(f"이미 존재하는 Security Agent Space: {sec_name}")

    sec_resp = sec_client.create_agent_space(
        name=sec_name,
        description=f"Security scanning for {description}",
        codeReviewSettings={"controlsScanning": True, "generalPurposeScanning": True},
    )
    sec_space_id = sec_resp["agentSpaceId"]

    # Connect GitHub repo via integration
    integration_id = ""
    warnings = []
    if not github_repo or "/" not in github_repo:
        warnings.append("GitHub 리포 미지정 — Security Space에 코드 스캔 연결 불가")
    else:
        owner = github_repo.split("/")[0]
        repo_name = github_repo.split("/")[-1]

        integs = sec_client.list_integrations(
            filter={"provider": "GITHUB"}
        ).get("integrationSummaries", [])
        if not integs:
            warnings.append("GitHub Integration 없음 — Security Space에 리포 연결 불가")
        else:
            integration_id = integs[0]["integrationId"]
            try:
                sec_client.update_integrated_resources(
                    agentSpaceId=sec_space_id,
                    integrationId=integration_id,
                    items=[{
                        "resource": {"githubRepository": {"name": repo_name, "owner": owner}},
                        "capabilities": {"github": {"leaveComments": True, "remediateCode": True}},
                    }],
                )
            except Exception as e:
                warnings.append(f"GitHub 리포 연결 실패: {e}")

    # Create Pentest if URL provided
    pentest_id = ""
    if pentest_url:
        try:
            service_role = _pentest_service_role()
            if not service_role:
                warnings.append("Service Role을 찾거나 생성할 수 없습니다. Pentest 생성을 건너뜁니다.")
                return {"space_id": sec_space_id, "name": sec_name, "pentest_id": "", "warnings": warnings}
            # Domain verification for private domains
            target_domain_ids = []
            if zone_id:
                import re
                m = re.search(r'https?://([^/:]+)', pentest_url)
                domain = m.group(1) if m else pentest_url.strip()
                try:
                    existing_domains = sec_client.list_target_domains().get("targetDomainSummaries", [])
                    td_match = next((td for td in existing_domains if td.get("domainName") == domain), None)
                    if td_match:
                        target_domain_ids = [td_match["targetDomainId"]]
                    else:
                        td_resp = sec_client.create_target_domain(targetDomainName=domain, verificationMethod="DNS_TXT")
                        td_id = td_resp["targetDomainId"]
                        target_domain_ids = [td_id]
                        dns_txt = td_resp.get("verificationDetails", {}).get("dnsTxt", {})
                        token = dns_txt.get("token", "")
                        record_name = dns_txt.get("dnsRecordName", f"_aws_securityagent-challenge.{domain}")
                        if token:
                            r53 = session.client("route53")
                            r53.change_resource_record_sets(
                                HostedZoneId=zone_id,
                                ChangeBatch={"Changes": [{"Action": "UPSERT", "ResourceRecordSet": {
                                    "Name": record_name, "Type": "TXT", "TTL": 300,
                                    "ResourceRecords": [{"Value": f'"{token}"'}],
                                }}]},
                            )
                        try:
                            sec_client.verify_target_domain(targetDomainId=td_id)
                        except Exception:
                            pass
                        # A 레코드 존재 확인
                        try:
                            r53_check = session.client("route53")
                            rr_resp = r53_check.list_resource_record_sets(
                                HostedZoneId=zone_id, StartRecordName=domain, StartRecordType="A", MaxItems="2")
                            has_a = any(rr["Name"].rstrip(".") == domain and rr["Type"] in ("A", "AAAA")
                                        for rr in rr_resp.get("ResourceRecordSets", []))
                            if not has_a:
                                warnings.append(f"'{domain}'의 A/Alias 레코드가 Route53 Zone에 없습니다. "
                                                f"NLB 등을 가리키는 A 레코드가 필요합니다.")
                        except Exception:
                            pass
                except Exception as e:
                    warnings.append(f"도메인 검증 경고: {e}")

            # Register awsResources + targetDomainIds
            aws_resources = {"iamRoles": [service_role]}
            if vpc_config:
                aws_resources["vpcs"] = [vpc_config]
            update_kwargs = {
                "agentSpaceId": sec_space_id,
                "name": sec_name,
                "awsResources": aws_resources,
            }
            if target_domain_ids:
                update_kwargs["targetDomainIds"] = target_domain_ids
            try:
                sec_client.update_agent_space(**update_kwargs)
            except Exception as e:
                warnings.append(f"awsResources 등록 경고: {e}")

            assets = {"endpoints": [{"uri": pentest_url}]}
            if integration_id and github_repo and "/" in github_repo:
                owner = github_repo.split("/")[0]
                repo_name = github_repo.split("/")[-1]
                assets["integratedRepositories"] = [{
                    "integrationId": integration_id,
                    "providerResourceId": f"{owner}/{repo_name}",
                }]
            kwargs = {
                "title": f"{sec_name}-pentest",
                "agentSpaceId": sec_space_id,
                "assets": assets,
                "excludeRiskTypes": ["DENIAL_OF_SERVICE"],
                "codeRemediationStrategy": "AUTOMATIC",
                "serviceRole": service_role,
            }
            if vpc_config:
                kwargs["vpcConfig"] = vpc_config
            pt_resp = sec_client.create_pentest(**kwargs)
            pentest_id = pt_resp.get("pentestId", "")
        except Exception as e:
            warnings.append(f"Pentest 생성 실패: {e}")

    if warnings:
        print(f"[SEC] Security Space 생성 경고: {warnings}", flush=True)
    return {"space_id": sec_space_id, "name": sec_name, "pentest_id": pentest_id, "warnings": warnings}


def _get_github_repo_id(client, service_id, owner, repo_name):
    """Find GitHub repo ID by scanning existing associations across all Spaces."""
    # Check existing associations for this repo
    try:
        spaces_resp = client.list_agent_spaces()
        for sp in spaces_resp.get("agentSpaces", []):
            sid = sp.get("agentSpaceId", "")
            assocs = client.list_associations(agentSpaceId=sid).get("associations", [])
            for a in assocs:
                cfg = a.get("configuration", {}).get("github", {})
                if cfg.get("repoName") == repo_name and cfg.get("owner") == owner:
                    return cfg["repoId"]
    except Exception:
        pass
    # Fallback: try public GitHub API
    import urllib.request
    url = f"https://api.github.com/repos/{owner}/{repo_name}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    return str(data["id"])


def _provision_agent_role(session, space_id, space_name, app_tag_value,
                          target_cluster_name=None, target_account_id=None):
    """Create IAM Role for DevOps Agent with proper trust policy + EKS access entry.

    Args:
        target_cluster_name: EKS cluster for AccessEntry (overrides config default)
        target_account_id: Account where the cluster lives (for cross-account AccessEntry)

    Returns role_arn or raises on failure.
    """
    iam = session.client("iam")
    account_id = _account_id(session)
    role_name = f"{space_name}-agent-role"

    trust_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "aidevops.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": account_id},
                "ArnLike": {"aws:SourceArn": f"arn:aws:aidevops:*:{account_id}:agentspace/*"},
            },
        }],
    })

    inline_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AgentSpaceApi",
                "Effect": "Allow",
                "Action": [
                    "aidevops:ListChats", "aidevops:CreateChat", "aidevops:SendMessage",
                    "aidevops:ListExecutions", "aidevops:ListJournalRecords",
                    "aidevops:GetAgentSpace", "aidevops:ListAssociations",
                    "aidevops:GetAssociation", "aidevops:DiscoverTopology",
                    "aidevops:ListGoals", "aidevops:ListRecommendations",
                    "aidevops:GetRecommendation", "aidevops:ListBacklogTasks",
                    "aidevops:GetBacklogTask", "aidevops:ListKnowledgeItems",
                    "aidevops:GetKnowledgeItem",
                ],
                "Resource": f"arn:aws:aidevops:*:{account_id}:agentspace/*",
            },
            {
                "Sid": "LogsSupplemental",
                "Effect": "Allow",
                "Action": ["logs:GetLogEvents"],
                "Resource": "*",
            },
        ],
    })

    # Create role
    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=trust_policy,
            Description=f"DevOps Agent role for Space {space_id}",
            Tags=[
                {"Key": "App", "Value": "DevOpsAgent"},
                {"Key": "SpaceId", "Value": space_id},
                {"Key": "TargetApp", "Value": app_tag_value},
            ],
        )
        role_arn = resp["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"

    # Attach managed policy
    iam.attach_role_policy(
        RoleName=role_name,
        PolicyArn="arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy",
    )

    # Put inline policy
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="DevOpsAgentApiPolicy",
        PolicyDocument=inline_policy,
    )

    # Create EKS Access Entry
    try:
        cluster_name = target_cluster_name or _cfg_get(_CFG, "kubernetes.cluster_name", "")
        if cluster_name:
            if target_account_id and target_account_id != account_id:
                from credential_resolver import credentials
                eks_session = credentials.get_session(target_account_id)
                eks = eks_session.client("eks")
            else:
                eks = session.client("eks")
            eks.create_access_entry(
                clusterName=cluster_name,
                principalArn=role_arn,
                type="STANDARD",
                tags={"App": "DevOpsAgent", "SpaceId": space_id},
            )
            eks.associate_access_policy(
                clusterName=cluster_name,
                principalArn=role_arn,
                policyArn="arn:aws:eks::aws:cluster-access-policy/AmazonEKSViewPolicy",
                accessScope={"type": "cluster"},
            )
    except Exception as e:
        print(f"EKS access entry warning (non-fatal): {e}", flush=True)

    return role_arn
