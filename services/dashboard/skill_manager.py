"""SkillManager — Agent Space skills via Asset API.

CRUD: create_asset, list_assets, update_asset, delete_asset
Cache: 60s in-memory TTL only. No disk cache, no sync.
"""
import hashlib
import io
import os
import sys
import time
import zipfile

import yaml

sys.path.insert(0, os.path.dirname(__file__))

_SKILLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../skills"))

_cache: dict = {}
_CACHE_TTL = 60


_AGENT_TYPE_MAP = {
    "Generic": "GENERIC",
    "Triage": "INCIDENT_TRIAGE",
    "Incident RCA": "INCIDENT_RCA",
    "RootCauseAnalysis": "INCIDENT_RCA",
    "GENERIC": "GENERIC",
    "INCIDENT_TRIAGE": "INCIDENT_TRIAGE",
    "INCIDENT_RCA": "INCIDENT_RCA",
}


def _content_hash(text: str) -> str:
    normalized = text.strip().replace("\r\n", "\n")
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


def _parse_frontmatter(content: str) -> dict:
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(content[3:end]) or {}
    except Exception:
        return {}


class SkillManager:
    def __init__(self):
        self._asset_clients = {}

    # ─── Asset API Client ────────────────────────────────────────────────

    def _get_asset_client(self, space_id: str):
        cache_key = space_id
        if cache_key in self._asset_clients:
            return self._asset_clients[cache_key]

        import boto3
        from botocore.config import Config
        from app_config import _profile_for_space, AWS_REGION

        profile = _profile_for_space(space_id)
        session = boto3.Session(profile_name=profile, region_name=AWS_REGION)
        client = session.client("devops-agent", config=Config(read_timeout=30, connect_timeout=10))
        self._asset_clients[cache_key] = client
        return client

    # ─── Remote Operations (Asset API) ───────────────────────────────────

    def list_remote(self, space_id: str) -> list:
        client = self._get_asset_client(space_id)
        resp = client.list_assets(agentSpaceId=space_id, assetType="skill")
        items = resp.get("items", [])
        return [self._parse_asset_item(a) for a in items]

    def _parse_asset_item(self, item: dict) -> dict:
        meta = item.get("metadata", {})
        return {
            "name": meta.get("name", ""),
            "knowledge_item_id": item.get("assetId", ""),
            "status": meta.get("status", "UNKNOWN"),
            "agent_types": meta.get("agent_types", []),
            "skill_type": meta.get("skill_type", "USER"),
            "description": meta.get("description", ""),
            "version": item.get("version", 1),
        }

    def get_remote(self, space_id: str, asset_id: str) -> str:
        client = self._get_asset_client(space_id)
        resp = client.get_asset_content(agentSpaceId=space_id, assetId=asset_id)
        zip_bytes = resp["content"]["zipFile"]
        if isinstance(zip_bytes, bytes):
            return self._extract_skill_md_from_zip(zip_bytes)
        return ""

    def _extract_skill_md_from_zip(self, zip_bytes: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            for name in zf.namelist():
                if name.endswith("SKILL.md") or name == "SKILL.md":
                    return zf.read(name).decode("utf-8")
        return ""

    def _build_if_needed(self, skill_name: str):
        src_dir = os.path.join(_SKILLS_DIR, skill_name, "src")
        if not os.path.isdir(src_dir):
            return
        modules = sorted(f for f in os.listdir(src_dir) if f.endswith(".md"))
        if not modules:
            return
        parts = []
        for fname in modules:
            with open(os.path.join(src_dir, fname), "r") as fh:
                parts.append(fh.read().rstrip())
        content = "\n\n".join(parts) + "\n"
        skill_file = os.path.join(_SKILLS_DIR, skill_name, "SKILL.md")
        with open(skill_file, "w") as fh:
            fh.write(content)

    def deploy(self, space_id: str, skill_name: str) -> dict:
        self._build_if_needed(skill_name)
        local_content = self.get_local(skill_name)
        if not local_content:
            return {"ok": False, "error": f"Local skill '{skill_name}' not found"}

        fm = _parse_frontmatter(local_content)
        agent_types = [_AGENT_TYPE_MAP.get(t, t) for t in fm.get("agent_types", ["Generic"])]
        if not agent_types:
            agent_types = ["GENERIC"]
        elif len(agent_types) > 1 and "GENERIC" in agent_types:
            agent_types.remove("GENERIC")

        remote_list = self.list_remote(space_id)
        existing = next((s for s in remote_list if s["name"] == skill_name), None)

        client = self._get_asset_client(space_id)
        zip_bytes = self._build_zip(skill_name)

        if existing:
            asset_id = existing["knowledge_item_id"]
            client.update_asset(
                agentSpaceId=space_id,
                assetId=asset_id,
                metadata={"agent_types": agent_types, "status": "ACTIVE"},
                content={"zip": {"zipFile": zip_bytes}},
            )
            return {"ok": True, "action": "updated", "asset_id": asset_id}
        else:
            resp = client.create_asset(
                agentSpaceId=space_id,
                assetType="skill",
                metadata={"agent_types": agent_types},
                content={"zip": {"zipFile": zip_bytes}},
            )
            asset_id = resp["asset"]["assetId"]
            return {"ok": True, "action": "created", "asset_id": asset_id}

    _ZIP_EXCLUDE_EXTS = {".sh", ".py", ".pyc", ".zip"}
    _ZIP_EXCLUDE_DIRS = {"src", "__pycache__", ".git"}

    def _build_zip(self, skill_name: str) -> bytes:
        skill_dir = os.path.join(_SKILLS_DIR, skill_name)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(skill_dir):
                dirs[:] = [d for d in dirs if d not in self._ZIP_EXCLUDE_DIRS]
                for fname in files:
                    if os.path.splitext(fname)[1].lower() in self._ZIP_EXCLUDE_EXTS:
                        continue
                    full_path = os.path.join(root, fname)
                    arc_name = os.path.relpath(full_path, skill_dir)
                    zf.write(full_path, arc_name)
        return buf.getvalue()

    def create_from_content(self, space_id: str, content: str) -> dict:
        """SKILL.md content를 직접 받아서 Agent Space에 등록 (파일시스템 불필요)."""
        fm = _parse_frontmatter(content)
        name = fm.get("name", "")
        if not name:
            return {"ok": False, "error": "SKILL.md에 name 필드 필요"}

        agent_types = [_AGENT_TYPE_MAP.get(t, t) for t in fm.get("agent_types", ["Generic"])]
        if not agent_types:
            agent_types = ["GENERIC"]
        elif len(agent_types) > 1 and "GENERIC" in agent_types:
            agent_types.remove("GENERIC")

        remote_list = self.list_remote(space_id)
        existing = next((s for s in remote_list if s["name"] == name), None)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("SKILL.md", content)
        zip_bytes = buf.getvalue()

        client = self._get_asset_client(space_id)

        if existing:
            asset_id = existing["knowledge_item_id"]
            client.update_asset(
                agentSpaceId=space_id,
                assetId=asset_id,
                metadata={"agent_types": agent_types, "status": "ACTIVE"},
                content={"zip": {"zipFile": zip_bytes}},
            )
            return {"ok": True, "action": "updated", "asset_id": asset_id, "name": name}
        else:
            resp = client.create_asset(
                agentSpaceId=space_id,
                assetType="skill",
                metadata={"agent_types": agent_types},
                content={"zip": {"zipFile": zip_bytes}},
            )
            asset_id = resp["asset"]["assetId"]
            return {"ok": True, "action": "created", "asset_id": asset_id, "name": name}

    def update_from_content(self, space_id: str, asset_id: str, content: str) -> dict:
        """기존 스킬 내용을 content로 직접 업데이트."""
        fm = _parse_frontmatter(content)
        agent_types = [_AGENT_TYPE_MAP.get(t, t) for t in fm.get("agent_types", ["Generic"])]
        if not agent_types:
            agent_types = ["GENERIC"]
        elif len(agent_types) > 1 and "GENERIC" in agent_types:
            agent_types.remove("GENERIC")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("SKILL.md", content)

        client = self._get_asset_client(space_id)
        client.update_asset(
            agentSpaceId=space_id,
            assetId=asset_id,
            metadata={"agent_types": agent_types, "status": "ACTIVE"},
            content={"zip": {"zipFile": buf.getvalue()}},
        )
        return {"ok": True, "asset_id": asset_id}

    def toggle(self, space_id: str, knowledge_item_id: str, enabled: bool) -> dict:
        status = "ACTIVE" if enabled else "INACTIVE"
        client = self._get_asset_client(space_id)
        client.update_asset(
            agentSpaceId=space_id,
            assetId=knowledge_item_id,
            metadata={"status": status},
        )
        return {"ok": True, "status": status}

    def update_agent_types(self, space_id: str, knowledge_item_id: str,
                           agent_types: list) -> dict:
        mapped = [_AGENT_TYPE_MAP.get(t, t) for t in agent_types]
        if "GENERIC" not in mapped:
            mapped.insert(0, "GENERIC")

        client = self._get_asset_client(space_id)
        client.update_asset(
            agentSpaceId=space_id,
            assetId=knowledge_item_id,
            metadata={"agent_types": mapped},
        )
        return {"ok": True, "agent_types": mapped}

    def delete_remote(self, space_id: str, knowledge_item_id: str) -> dict:
        client = self._get_asset_client(space_id)
        client.delete_asset(agentSpaceId=space_id, assetId=knowledge_item_id)
        return {"ok": True}

    # ─── Auto-deploy ────────────────────────────────────────────────────

    DEFAULT_SKILLS = ["arch-discover", "k8s-detail"]

    def ensure_default_skills(self, space_id: str) -> dict:
        """분석 전 필수 스킬이 배포되어 있는지 확인. 미배포 시 자동 deploy."""
        remote = self.list_remote(space_id)
        remote_names = {s["name"] for s in remote}
        deployed = []
        failed = []
        for skill_name in self.DEFAULT_SKILLS:
            if skill_name in remote_names:
                continue
            print(f"[SKILL] 기본 스킬 자동 배포: {skill_name}")
            try:
                result = self.deploy(space_id, skill_name)
                if result.get("ok"):
                    deployed.append(skill_name)
                else:
                    failed.append(skill_name)
                    print(f"[SKILL] 배포 실패: {skill_name} — {result.get('error', '')}")
            except Exception as e:
                failed.append(skill_name)
                print(f"[SKILL] 배포 실패: {skill_name} — {e}")
        return {"deployed": deployed, "failed": failed, "already": list(remote_names & set(self.DEFAULT_SKILLS))}

    # ─── Local Operations (Developer Workflow) ───────────────────────────

    def list_local(self) -> list:
        results = []
        if not os.path.isdir(_SKILLS_DIR):
            return results
        for name in sorted(os.listdir(_SKILLS_DIR)):
            skill_file = os.path.join(_SKILLS_DIR, name, "SKILL.md")
            if not os.path.isfile(skill_file):
                continue
            with open(skill_file, "r") as f:
                content = f.read()
            fm = _parse_frontmatter(content)
            results.append({
                "name": fm.get("name", name),
                "description": fm.get("description", ""),
                "agent_types": fm.get("agent_types", ["Generic"]),
                "version": fm.get("version", ""),
                "path": skill_file,
                "content_hash": _content_hash(content),
            })
        return results

    def get_local(self, skill_name: str) -> str:
        skill_file = os.path.join(_SKILLS_DIR, skill_name, "SKILL.md")
        if not os.path.isfile(skill_file):
            return ""
        with open(skill_file, "r") as f:
            return f.read()

    def save_local(self, skill_name: str, content: str):
        skill_dir = os.path.join(_SKILLS_DIR, skill_name)
        os.makedirs(skill_dir, exist_ok=True)
        skill_file = os.path.join(skill_dir, "SKILL.md")
        with open(skill_file, "w") as f:
            f.write(content)

    # ─── Cache (60s in-memory only) ────────────────────────────────────

    def list_skills(self, space_id: str) -> list:
        """Return skills from API. Uses 60s TTL cache."""
        entry = _cache.get(space_id)
        if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
            return entry["skills"]
        skills = self.list_remote(space_id)
        _cache[space_id] = {"skills": skills, "ts": time.time()}
        return skills

    def invalidate_cache(self, space_id: str):
        """Drop cache so next list_skills() fetches fresh."""
        _cache.pop(space_id, None)


_manager = None


def get_skill_manager() -> SkillManager:
    global _manager
    if _manager is None:
        _manager = SkillManager()
    return _manager
