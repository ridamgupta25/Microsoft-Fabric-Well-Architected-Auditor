"""GET /api/config - pillars and auth defaults the front end needs at startup."""
from __future__ import annotations

import os

from flask import Blueprint, current_app, jsonify

from ...core.models import PILLARS
from ...services import audit_service

bp = Blueprint("config", __name__)


def _clean(value):
    return "" if (not value or str(value).startswith("<")) else value


@bp.get("/api/config")
def get_config():
    default_project = current_app.config["DEFAULT_PROJECT"]
    try:
        proj, _settings, _base, _path = audit_service.load_project(default_project)
        auth_cfg = proj.get("auth", {}) or {}
    except Exception:
        auth_cfg = {}
    return jsonify({
        "project": default_project,
        "project_name": os.path.basename(default_project),
        "pillars": PILLARS,
        "auth": {
            "tenant_id": _clean(auth_cfg.get("tenant_id")),
            "client_id": _clean(auth_cfg.get("client_id")),
        },
    })
