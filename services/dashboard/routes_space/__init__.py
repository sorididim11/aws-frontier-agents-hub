"""
routes_space package — split from routes_space.py for maintainability.

The Blueprint `space_bp` is created here and imported by all sub-modules.
"""
from flask import Blueprint

space_bp = Blueprint("space_bp", __name__)

# Import sub-modules to register routes on space_bp
from routes_space import core  # noqa: E402, F401
from routes_space import discover  # noqa: E402, F401
from routes_space import integrations  # noqa: E402, F401
from routes_space import accounts  # noqa: E402, F401
from routes_space import settings  # noqa: E402, F401
from routes_space import deploy  # noqa: E402, F401

# Re-export shared helpers for external consumers
# (routes_space_cfn_import imports _get_space_meta, datasource_manager imports api_generate_cfn_internal)
from routes_space.core import _get_space_meta, _save_space_metadata, _setup_event_channel, _append_integration  # noqa: E402, F401
from routes_space.deploy import api_generate_cfn_internal  # noqa: E402, F401
