"""CatalogManager — GitHub AWS skill catalog indexing, caching, and recommendation.

Uses git sparse-checkout to fetch only SKILL.md files from the
aws-samples/sample-ai-agent-skills repo. Parses frontmatter locally
for descriptions and categories. Caches to disk; refresh is manual only.
"""
import json
import os
import re
import subprocess
import time

_CACHE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../.skill-cache"))
_CATALOG_CACHE = os.path.join(_CACHE_DIR, "catalog.json")

_REPO_URL = "https://github.com/aws-samples/sample-ai-agent-skills.git"
_REPO = "aws-samples/sample-ai-agent-skills"
_LOCAL_CLONE = os.path.join(_CACHE_DIR, "repo")

SERVICE_CATEGORY_MAP = {
    # Containers & Orchestration
    "eks": "Containers", "ecs": "Containers", "fargate": "Containers",
    "ecr": "Containers", "apprunner": "Containers", "bottlerocket": "Containers",
    "proton": "Containers",
    # Compute
    "lambda": "Compute", "ec2": "Compute", "batch": "Compute",
    "lightsail": "Compute", "outposts": "Compute",
    "wavelength": "Compute", "autoscaling": "Compute", "elasticbeanstalk": "Compute",
    "parallelcluster": "Compute",
    # Database
    "dynamodb": "Database", "aurora": "Database", "rds": "Database",
    "elasticache": "Database", "neptune": "Database", "documentdb": "Database",
    "keyspaces": "Database", "memorydb": "Database", "timestream": "Database",
    "qldb": "Database", "redshift": "Database",
    # Networking
    "vpc": "Networking", "elb": "Networking", "cloudfront": "Networking",
    "route53": "Networking", "directconnect": "Networking", "transitgateway": "Networking",
    "vpn": "Networking", "globalaccelerator": "Networking", "appmesh": "Networking",
    "vpclattice": "Networking", "privatelink": "Networking",
    "networkfirewall": "Networking", "verified-access": "Networking",
    "verifiedaccess": "Networking",
    # Storage
    "s3": "Storage", "efs": "Storage", "fsx": "Storage",
    "storagegateway": "Storage", "backup": "Storage", "datasync": "Storage",
    "transfer-family": "Storage",
    # Security & Identity
    "iam": "Security", "cognito": "Security", "waf": "Security",
    "guardduty": "Security", "securityhub": "Security", "inspector": "Security",
    "macie": "Security", "kms": "Security", "secretsmanager": "Security",
    "acm": "Security", "sso": "Security", "accessanalyzer": "Security",
    "detective": "Security", "firewall": "Security", "shield": "Security",
    "verifiedpermissions": "Security", "auditmanager": "Security",
    # Application Integration
    "sqs": "Integration", "sns": "Integration", "eventbridge": "Integration",
    "stepfunctions": "Integration", "mq": "Integration", "kinesis": "Integration",
    "eventdriven": "Integration", "appsync": "Integration", "apigateway": "Integration",
    "appflow": "Integration",
    # Management & Monitoring
    "cloudwatch": "Monitoring", "xray": "Monitoring", "cloudtrail": "Monitoring",
    "config": "Monitoring", "ssm": "Management", "organizations": "Management",
    "controlTower": "Management", "servicecatalog": "Management",
    "trustedadvisor": "Management", "healthdashboard": "Management",
    "systems-manager": "Management", "changemanager": "Management",
    # DevOps / CI/CD
    "cicd": "DevOps", "codepipeline": "DevOps", "codecommit": "DevOps",
    "codebuild": "DevOps", "codedeploy": "DevOps", "codeartifact": "DevOps",
    "cloudformation": "DevOps", "cdk": "DevOps",
    # AI/ML
    "sagemaker": "AI/ML", "bedrock": "AI/ML", "comprehend": "AI/ML",
    "rekognition": "AI/ML", "textract": "AI/ML", "transcribe": "AI/ML",
    "translate": "AI/ML", "polly": "AI/ML", "lex": "AI/ML",
    "personalize": "AI/ML", "forecast": "AI/ML", "braket": "AI/ML",
    # Analytics
    "athena": "Analytics", "glue": "Analytics", "emr": "Analytics",
    "opensearch": "Analytics", "lakeformation": "Analytics",
    "quicksight": "Analytics", "msk": "Analytics", "datazone": "Analytics",
    # End User / Workspace
    "workspaces": "Workspace", "appstream": "Workspace", "workdocs": "Workspace",
    "workmail": "Workspace", "wickr": "Workspace", "chimesdk": "Workspace",
    "connect": "Workspace", "chatbot": "Workspace",
    # IoT
    "iot": "IoT", "greengrass": "IoT", "sitewise": "IoT",
    # Migration
    "dms": "Migration", "migration": "Migration", "appdiscovery": "Migration",
    "mgn": "Migration",
    # Other
    "amplify": "Frontend", "location": "Frontend",
    "application-composer": "Frontend",
}

KIND_TO_CATALOG = {
    "amazon eks": "eks-troubleshooting",
    "amazon ecs": "ecs-troubleshooting",
    "aws lambda": "lambda-troubleshooting",
    "amazon dynamodb": "dynamodb-troubleshooting",
    "amazon sqs": "eventdriven-troubleshooting",
    "amazon sns": "sns-advanced-troubleshooting",
    "amazon cloudwatch": "cloudwatch-troubleshooting",
    "amazon s3": "s3-troubleshooting",
    "amazon rds": "rds-troubleshooting",
    "amazon aurora": "aurora-troubleshooting",
    "amazon elasticache": "elasticache-troubleshooting",
    "amazon api gateway": "apigateway-troubleshooting",
    "aws step functions": "stepfunctions-troubleshooting",
    "amazon kinesis": "kinesis-troubleshooting",
    "amazon cloudfront": "cloudfront-troubleshooting",
    "amazon ecr": "ecr-troubleshooting",
    "aws fargate": "fargate-troubleshooting",
    "amazon vpc": "vpc-troubleshooting",
    "elastic load balancing": "elb-troubleshooting",
    "amazon ec2": "ec2-troubleshooting",
    "aws iam": "iam-troubleshooting",
    "amazon cognito": "cognito-troubleshooting",
    "aws secrets manager": "secretsmanager-troubleshooting",
    "amazon eventbridge": "eventbridge-troubleshooting",
    "amazon opensearch": "opensearch-troubleshooting",
    "amazon msk": "msk-troubleshooting",
    "aws glue": "glue-troubleshooting",
    "amazon bedrock": "bedrock-troubleshooting",
    "amazon sagemaker": "sagemaker-troubleshooting",
    "aws codepipeline": "cicd-troubleshooting",
    "aws cloudformation": "cloudformation-troubleshooting",
    "amazon route 53": "route53-troubleshooting",
    "aws waf": "waf-shield-troubleshooting",
    "amazon efs": "efs-troubleshooting",
}


def _parse_skill_description(content: str) -> str:
    """Extract description from SKILL.md frontmatter."""
    # Multi-line: description: >\n  line1\n  line2
    m = re.search(r'^description:\s*>?\s*\n((?:[ \t]+.+\n)+)', content, re.MULTILINE)
    if m:
        return re.sub(r'\s+', ' ', m.group(1)).strip()
    # Single-line: description: some text
    m2 = re.search(r'^description:\s*(.+)$', content, re.MULTILINE)
    if m2:
        return m2.group(1).strip()
    return ""


def _categorize(folder_name: str) -> str:
    prefix = folder_name.replace("-troubleshooting", "").replace("-advanced", "")
    for key, cat in SERVICE_CATEGORY_MAP.items():
        if prefix == key or prefix.startswith(key):
            return cat
    return "Other"


def _service_name_from_folder(folder_name: str) -> str:
    base = folder_name.replace("-troubleshooting", "").replace("-advanced", "")
    parts = base.split("-")
    return " ".join(p.capitalize() for p in parts)


def _gh_api(path: str, jq_filter: str = None) -> str:
    cmd = ["gh", "api", path]
    if jq_filter:
        cmd += ["--jq", jq_filter]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"gh api failed: {result.stderr[:200]}")
    return result.stdout


class CatalogManager:
    def __init__(self):
        self._index = None

    def get_index(self, force_refresh=False) -> dict:
        """캐시 반환. force_refresh=True면 git pull + 재빌드."""
        if force_refresh:
            self._index = self._fetch_index()
            self._save_disk_cache(self._index)
            return self._index

        if self._index:
            return self._index

        cached = self._load_disk_cache()
        if cached:
            self._index = cached
            return cached

        self._index = self._fetch_index()
        self._save_disk_cache(self._index)
        return self._index

    def get_skill_detail(self, folder_name: str) -> dict:
        skill_md = _gh_api(
            f"repos/{_REPO}/contents/{folder_name}/SKILL.md",
            ".content"
        )
        import base64
        content = base64.b64decode(skill_md).decode("utf-8")

        try:
            refs_raw = _gh_api(
                f"repos/{_REPO}/git/trees/main:{folder_name}/references",
                '[.tree[] | .path]'
            )
            references = json.loads(refs_raw)
        except Exception:
            references = []

        return {
            "folder_name": folder_name,
            "skill_md": content,
            "references": references,
        }

    def _fetch_reference(self, folder_name: str, ref_name: str) -> str:
        import base64
        raw = _gh_api(
            f"repos/{_REPO}/contents/{folder_name}/references/{ref_name}",
            ".content"
        )
        return base64.b64decode(raw).decode("utf-8")

    def recommend(self, nodes: list, deployed_names: set) -> list:
        index = self.get_index()
        skills_map = index.get("skills", {})
        recommendations = []
        seen = set()

        for node in nodes:
            kind = (node.get("kind") or "").lower()
            for prefix, catalog_name in KIND_TO_CATALOG.items():
                if kind.startswith(prefix):
                    if catalog_name in seen or catalog_name in deployed_names:
                        continue
                    if catalog_name not in skills_map:
                        continue
                    seen.add(catalog_name)
                    skill = skills_map[catalog_name]
                    recommendations.append({
                        "folder_name": catalog_name,
                        "service_name": skill.get("service_name", ""),
                        "description": skill.get("description", ""),
                        "category": skill.get("category", "Other"),
                        "reference_count": skill.get("reference_count", 0),
                        "match_reason": f"토폴로지에 '{node.get('kind', '')}' 노드 발견",
                    })
                    break

        return recommendations

    def _fetch_index(self) -> dict:
        # 1. Ensure local sparse clone exists (SKILL.md only)
        self._ensure_repo()

        # 2. Get reference info from GitHub Tree API (for runbook counts + shas)
        tree_json = _gh_api(
            f"repos/{_REPO}/git/trees/main?recursive=1",
            '[.tree[] | {path: .path, type: .type, sha: .sha}]'
        )
        all_entries = json.loads(tree_json)

        folder_shas = {}
        ref_files_by_folder = {}

        for entry in all_entries:
            path = entry["path"]
            parts = path.split("/")

            if entry["type"] == "tree" and len(parts) == 1 and path.endswith("-troubleshooting"):
                folder_shas[path] = entry["sha"]

            if entry["type"] == "blob" and len(parts) == 3 and parts[1] == "references":
                folder = parts[0]
                if folder not in ref_files_by_folder:
                    ref_files_by_folder[folder] = []
                ref_files_by_folder[folder].append(parts[2])

        # 3. Parse SKILL.md descriptions from local clone
        skills = {}
        categories = {}

        for folder, sha in folder_shas.items():
            category = _categorize(folder)
            service_name = _service_name_from_folder(folder)
            description = self._read_local_description(folder)

            ref_files = ref_files_by_folder.get(folder, [])
            domains = self._parse_domains(ref_files)

            skills[folder] = {
                "folder_name": folder,
                "service_name": service_name,
                "description": description,
                "category": category,
                "domains": domains,
                "reference_count": len(ref_files),
                "sha": sha,
            }

            if category not in categories:
                categories[category] = []
            categories[category].append(folder)

        repo_sha = self._get_local_sha()
        return {
            "version": 2,
            "repo_sha": repo_sha,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "skill_count": len(skills),
            "categories": categories,
            "skills": skills,
        }

    def _get_local_sha(self) -> str:
        """로컬 clone의 HEAD sha."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=_LOCAL_CLONE, capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    def _ensure_repo(self):
        """Sparse clone if not exists, git pull if exists."""
        git_dir = os.path.join(_LOCAL_CLONE, ".git")
        if os.path.isdir(git_dir):
            # sparse-checkout 상태 확인 — SKILL.md 없으면 재설정
            test_file = next(
                (f for f in os.listdir(_LOCAL_CLONE)
                 if os.path.isdir(os.path.join(_LOCAL_CLONE, f))
                 and os.path.isfile(os.path.join(_LOCAL_CLONE, f, "SKILL.md"))),
                None,
            )
            if not test_file:
                subprocess.run(
                    ["git", "sparse-checkout", "set", "--no-cone", "*/SKILL.md"],
                    cwd=_LOCAL_CLONE, capture_output=True, timeout=30,
                )
            subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=_LOCAL_CLONE, capture_output=True, timeout=30,
            )
            return

        os.makedirs(_CACHE_DIR, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", _REPO_URL, _LOCAL_CLONE],
            capture_output=True, timeout=60,
        )
        subprocess.run(
            ["git", "sparse-checkout", "set", "--no-cone", "*/SKILL.md"],
            cwd=_LOCAL_CLONE, capture_output=True, timeout=30,
        )

    def _read_local_description(self, folder: str) -> str:
        """Read description from local SKILL.md clone."""
        skill_path = os.path.join(_LOCAL_CLONE, folder, "SKILL.md")
        if not os.path.isfile(skill_path):
            return ""
        try:
            with open(skill_path, "r") as f:
                content = f.read(4096)
            return _parse_skill_description(content)
        except Exception:
            return ""

    def _parse_domains(self, ref_files: list) -> list:
        domain_map = {}
        for f in ref_files:
            match = re.match(r'^([A-Z])(\d+)-(.+)\.md$', f)
            if match:
                domain_id = match.group(1)
                runbook_name = f
                if domain_id not in domain_map:
                    domain_map[domain_id] = {"id": domain_id, "runbooks": []}
                domain_map[domain_id]["runbooks"].append(runbook_name)
        return sorted(domain_map.values(), key=lambda d: d["id"])

    def _load_disk_cache(self) -> dict | None:
        if not os.path.isfile(_CATALOG_CACHE):
            return None
        try:
            with open(_CATALOG_CACHE, "r") as f:
                return json.load(f)
        except Exception:
            return None

    def _save_disk_cache(self, data: dict):
        os.makedirs(_CACHE_DIR, exist_ok=True)
        try:
            with open(_CATALOG_CACHE, "w") as f:
                json.dump(data, f)
        except Exception:
            pass


_catalog = None


def get_catalog_manager() -> CatalogManager:
    global _catalog
    if _catalog is None:
        _catalog = CatalogManager()
    return _catalog
