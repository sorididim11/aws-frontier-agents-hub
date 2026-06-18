"""
Scenario routes — extracted from overview_app.py as a Flask Blueprint.

Includes:
- Scenario Generation from Recommendations (generate, validate, save)
- Scenario Tab — List, Detail, Run Proxy, Chat, Evaluate, Improvements
"""
from flask import Blueprint

from app_config import (
    _CFG, _cfg_get, AWS_REGION, AGENT_SPACE_ID, RUNS_TABLE, AWS_PROFILE,
    _req_space_id, _agent_space_id, _boto_session,
    AVAILABLE_MODELS,
)
from routes_arch import (
    _arch_table,
    _load_latest_arch, _list_scenarios, _get_scenario, _save_scenario,
    _delete_scenario,
)

scenario_bp = Blueprint("scenario_bp", __name__)


def _get_space_app_names(space_id: str) -> set:
    """Space에 속한 앱/서비스 이름 목록. 토폴로지 결과 → App 태그 fallback.

    group 이름 + 개별 서비스 이름을 모두 포함하여 보안 시나리오 매칭에 사용.
    """
    apps = set()
    try:
        saved = _load_latest_arch(space_id)
        if saved:
            nodes = saved.get("graph", {}).get("nodes", [])
            for n in nodes:
                if n.get("service_type") == "boundary":
                    continue
                g = n.get("group", "")
                if g:
                    apps.add(g)
                name = n.get("name", "")
                if name:
                    apps.add(name)
            if apps:
                return apps
    except Exception:
        pass
    try:
        from app_config import _tag_value_for_space
        tag_val = _tag_value_for_space(space_id)
        if tag_val:
            apps.add(tag_val)
    except Exception:
        pass
    return apps


def _app_matches_space(sec_app: str, space_apps: set) -> bool:
    """보안 시나리오의 app_name이 Space의 앱/서비스와 매칭되는지 확인."""
    sec_lower = sec_app.lower()
    if sec_app in space_apps:
        return True
    for app in space_apps:
        if sec_lower in app.lower() or app.lower() in sec_lower:
            return True
    return False


# Import sub-modules to register routes on scenario_bp
from routes_scenario import generate  # noqa: E402, F401
from routes_scenario import crud  # noqa: E402, F401
from routes_scenario import run  # noqa: E402, F401
from routes_scenario import evaluate  # noqa: E402, F401
from routes_scenario import chat  # noqa: E402, F401
