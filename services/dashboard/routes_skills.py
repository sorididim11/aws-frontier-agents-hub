"""Routes — Skills management API + AWS Catalog + Topology Recommendation.

모든 스킬 CRUD는 Asset API 기반. 로컬 파일시스템 의존 없음.
"""
import os
import sys

from flask import Blueprint, jsonify, request

sys.path.insert(0, os.path.dirname(__file__))
from skill_manager import get_skill_manager
from catalog_manager import get_catalog_manager
from app_config import _req_space_id

skills_bp = Blueprint("skills_bp", __name__)


# ─── Space Skills CRUD (Asset API) ────────────────────────────────────────────

@skills_bp.route("/api/skills")
def api_skills_list():
    """Space에 등록된 스킬 목록."""
    space_id = _req_space_id()
    if not space_id:
        return jsonify({"ok": False, "error": "space_id required"}), 400

    mgr = get_skill_manager()
    skills = mgr.list_skills(space_id)
    return jsonify({"ok": True, "skills": skills})


@skills_bp.route("/api/skills/refresh", methods=["POST"])
def api_skills_refresh():
    """캐시 무효화 후 재조회."""
    data = request.get_json(force=True) if request.is_json else {}
    space_id = data.get("space_id") or _req_space_id()
    if not space_id:
        return jsonify({"ok": False, "error": "space_id required"}), 400

    mgr = get_skill_manager()
    try:
        mgr.invalidate_cache(space_id)
        skills = mgr.list_skills(space_id)
        return jsonify({"ok": True, "skills": skills})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@skills_bp.route("/api/skills/create", methods=["POST"])
def api_skills_create():
    """스킬 생성 — content(SKILL.md) 전달 → Agent Space 등록."""
    data = request.get_json(force=True)
    space_id = data.get("space_id") or _req_space_id()
    content = data.get("content", "").strip()

    if not space_id:
        return jsonify({"ok": False, "error": "space_id required"}), 400
    if not content:
        return jsonify({"ok": False, "error": "content required"}), 400

    mgr = get_skill_manager()
    try:
        result = mgr.create_from_content(space_id, content)
        if result.get("ok"):
            mgr.invalidate_cache(space_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@skills_bp.route("/api/skills/<knowledge_item_id>", methods=["GET"])
def api_skills_content(knowledge_item_id):
    """스킬 내용 조회."""
    space_id = _req_space_id()
    if not space_id:
        return jsonify({"ok": False, "error": "space_id required"}), 400
    mgr = get_skill_manager()
    try:
        content = mgr.get_remote(space_id, knowledge_item_id)
        return jsonify({"ok": True, "knowledge_item_id": knowledge_item_id, "content": content})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@skills_bp.route("/api/skills/<knowledge_item_id>", methods=["PUT"])
def api_skills_update(knowledge_item_id):
    """스킬 수정 — content 전달 → Agent Space 업데이트."""
    data = request.get_json(force=True)
    space_id = data.get("space_id") or _req_space_id()
    content = data.get("content", "").strip()

    if not space_id:
        return jsonify({"ok": False, "error": "space_id required"}), 400
    if not content:
        return jsonify({"ok": False, "error": "content required"}), 400

    mgr = get_skill_manager()
    try:
        result = mgr.update_from_content(space_id, knowledge_item_id, content)
        if result.get("ok"):
            mgr.invalidate_cache(space_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@skills_bp.route("/api/skills/<knowledge_item_id>", methods=["DELETE"])
def api_skills_delete(knowledge_item_id):
    """스킬 삭제."""
    space_id = request.args.get("space_id") or _req_space_id()
    if not space_id:
        return jsonify({"ok": False, "error": "space_id required"}), 400

    mgr = get_skill_manager()
    try:
        result = mgr.delete_remote(space_id, knowledge_item_id)
        if result.get("ok"):
            mgr.invalidate_cache(space_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@skills_bp.route("/api/skills/toggle", methods=["POST"])
def api_skills_toggle():
    """스킬 활성/비활성 전환."""
    data = request.get_json(force=True)
    space_id = data.get("space_id") or _req_space_id()
    kid = data.get("knowledge_item_id", "")
    enabled = data.get("enabled", True)

    if not space_id or not kid:
        return jsonify({"ok": False, "error": "space_id and knowledge_item_id required"}), 400

    mgr = get_skill_manager()
    try:
        result = mgr.toggle(space_id, kid, enabled)
        if result.get("ok"):
            mgr.invalidate_cache(space_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@skills_bp.route("/api/skills/update-agent-types", methods=["POST"])
def api_skills_update_agent_types():
    """스킬 agent_types 변경."""
    data = request.get_json(force=True)
    space_id = data.get("space_id") or _req_space_id()
    kid = data.get("knowledge_item_id", "")
    agent_types = data.get("agent_types", [])

    if not space_id or not kid:
        return jsonify({"ok": False, "error": "space_id and knowledge_item_id required"}), 400

    mgr = get_skill_manager()
    try:
        result = mgr.update_agent_types(space_id, kid, agent_types)
        if result.get("ok"):
            mgr.invalidate_cache(space_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@skills_bp.route("/api/skills/generate", methods=["POST"])
def api_skills_generate():
    """AI 스킬 초안 생성."""
    data = request.get_json(force=True)
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"ok": False, "error": "prompt required"}), 400

    from ai_provider import get_provider
    provider = get_provider()

    full_prompt = (
        "당신은 DevOps Agent 스킬 작성 전문가입니다. "
        "아래 요청에 따라 SKILL.md 파일을 생성하세요.\n\n"
        "포맷 규칙:\n"
        "1. YAML frontmatter (---로 감싸기): name(kebab-case), description(1-3줄), agent_types 필수\n"
        "2. agent_types: Generic, Incident RCA, INCIDENT_TRIAGE 중 선택\n"
        "3. 본문: # 제목, ## 섹션들로 구성\n"
        "4. 행동규칙형: ## 핵심 규칙, ## 적용 조건 등\n"
        "5. 구조화응답형: ## 트리거 (`#keyword`), ## 역할, ## 출력 포맷 (JSON 코드블록)\n"
        "6. 모든 텍스트는 한국어\n\n"
        "SKILL.md 내용만 출력하세요. 코드블록으로 감싸지 마세요.\n\n"
        f"요청: {prompt}"
    )

    try:
        result = provider.generate(full_prompt)
        content = result.get("reply", "") if isinstance(result, dict) else str(result)
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content
        if content.endswith("```"):
            content = content[:-3]
        return jsonify({"ok": True, "content": content.strip()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── AWS Skill Catalog ──────────────────────────────────────────────────────

@skills_bp.route("/api/skills/catalog")
def api_skills_catalog():
    """카탈로그 인덱스 반환 (캐시)."""
    cat = get_catalog_manager()
    try:
        index = cat.get_index()
        return jsonify({"ok": True, **index})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@skills_bp.route("/api/skills/catalog/refresh", methods=["POST"])
def api_skills_catalog_refresh():
    """카탈로그 수동 갱신 (git pull + 재빌드)."""
    cat = get_catalog_manager()
    try:
        index = cat.get_index(force_refresh=True)
        return jsonify({"ok": True, **index})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@skills_bp.route("/api/skills/catalog/<folder_name>")
def api_skills_catalog_detail(folder_name):
    """카탈로그 스킬 상세 (SKILL.md + references)."""
    cat = get_catalog_manager()
    try:
        detail = cat.get_skill_detail(folder_name)
        return jsonify({"ok": True, **detail})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@skills_bp.route("/api/skills/catalog/<folder_name>/deploy", methods=["POST"])
def api_skills_catalog_deploy(folder_name):
    """카탈로그 스킬을 GitHub에서 다운 → Agent Space 등록."""
    data = request.get_json(force=True) if request.is_json else {}
    space_id = data.get("space_id") or _req_space_id()
    if not space_id:
        return jsonify({"ok": False, "error": "space_id required"}), 400

    cat = get_catalog_manager()
    mgr = get_skill_manager()

    try:
        detail = cat.get_skill_detail(folder_name)
        skill_md = detail["skill_md"]
        references = detail["references"]

        import io, zipfile
        from skill_manager import _parse_frontmatter, _AGENT_TYPE_MAP

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("SKILL.md", skill_md)
            for ref_name in references:
                try:
                    ref_content = cat._fetch_reference(folder_name, ref_name)
                    zf.writestr(f"references/{ref_name}", ref_content)
                except Exception:
                    pass

        fm = _parse_frontmatter(skill_md)
        agent_types = [_AGENT_TYPE_MAP.get(t, t) for t in fm.get("agent_types", ["Generic"])]
        if "GENERIC" not in agent_types:
            agent_types.insert(0, "GENERIC")

        client = mgr._get_asset_client(space_id)
        resp = client.create_asset(
            agentSpaceId=space_id,
            assetType="skill",
            metadata={"agent_types": agent_types},
            content={"zip": {"zipFile": buf.getvalue()}},
        )
        asset_id = resp["asset"]["assetId"]
        mgr.invalidate_cache(space_id)
        return jsonify({"ok": True, "asset_id": asset_id, "folder_name": folder_name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── Topology-based Recommendation ──────────────────────────────────────────

@skills_bp.route("/api/skills/recommend")
def api_skills_recommend():
    """토폴로지 기반 추천."""
    space_id = _req_space_id()
    if not space_id:
        return jsonify({"ok": False, "error": "space_id required"}), 400

    mgr = get_skill_manager()
    cat = get_catalog_manager()

    deployed = mgr.list_remote(space_id)
    deployed_names = {s["name"] for s in deployed}

    nodes = _get_topology_nodes(space_id)
    if not nodes:
        return jsonify({"ok": True, "recommendations": [], "reason": "토폴로지 분석 결과 없음"})

    recommendations = cat.recommend(nodes, deployed_names)
    return jsonify({"ok": True, "recommendations": recommendations})


@skills_bp.route("/api/skills/recommend/apply", methods=["POST"])
def api_skills_recommend_apply():
    """추천 스킬 일괄 등록."""
    data = request.get_json(force=True)
    space_id = data.get("space_id") or _req_space_id()
    folder_names = data.get("folder_names", [])

    if not space_id or not folder_names:
        return jsonify({"ok": False, "error": "space_id and folder_names[] required"}), 400

    import io, zipfile
    from skill_manager import _parse_frontmatter, _AGENT_TYPE_MAP

    cat = get_catalog_manager()
    mgr = get_skill_manager()
    results = {}

    for folder in folder_names:
        try:
            detail = cat.get_skill_detail(folder)

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("SKILL.md", detail["skill_md"])
                for ref_name in detail["references"]:
                    try:
                        ref_content = cat._fetch_reference(folder, ref_name)
                        zf.writestr(f"references/{ref_name}", ref_content)
                    except Exception:
                        pass

            fm = _parse_frontmatter(detail["skill_md"])
            agent_types = [_AGENT_TYPE_MAP.get(t, t) for t in fm.get("agent_types", ["Generic"])]
            if "GENERIC" not in agent_types:
                agent_types.insert(0, "GENERIC")

            client = mgr._get_asset_client(space_id)
            resp = client.create_asset(
                agentSpaceId=space_id,
                assetType="skill",
                metadata={"agent_types": agent_types},
                content={"zip": {"zipFile": buf.getvalue()}},
            )
            results[folder] = {"ok": True, "asset_id": resp["asset"]["assetId"]}
        except Exception as e:
            results[folder] = {"ok": False, "error": str(e)}

    mgr.invalidate_cache(space_id)
    all_ok = all(r.get("ok") for r in results.values())
    return jsonify({"ok": all_ok, "results": results})


def _get_topology_nodes(space_id: str) -> list:
    """DDB에서 최신 아키텍처 분석 노드 로드."""
    try:
        from app_config import _boto_session
        from boto3.dynamodb.conditions import Key

        session = _boto_session()
        ddb = session.resource("dynamodb")
        table = ddb.Table("frontier-agent-hub-scenario-runs")

        resp = table.query(
            IndexName="scenario-id-index",
            KeyConditionExpression=Key("scenario_id").eq(space_id) & Key("run_id").begins_with("arch-2"),
            ScanIndexForward=False,
            Limit=1,
        )
        items = resp.get("Items", [])
        if not items:
            return []

        item = items[0]
        nodes = item.get("graph", {}).get("nodes", [])
        if nodes:
            return nodes
        nodes = item.get("nodes", [])
        if nodes:
            return nodes
        for app in item.get("apps", []):
            nodes.extend(app.get("nodes", []))
        return nodes
    except Exception as e:
        print(f"[SKILL-RECOMMEND] topology node fetch failed: {e}", flush=True)
    return []
