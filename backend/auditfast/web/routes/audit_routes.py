"""POST /api/run - execute an audit and return the scorecard JSON."""
from __future__ import annotations

import traceback

from flask import Blueprint, current_app, jsonify, request

from ...services import audit_service, auth_service

bp = Blueprint("audit", __name__)


@bp.post("/api/run")
def run():
    body = request.get_json(silent=True) or {}

    # The UI sends [{id, role, name}, ...]; older callers may send bare ids.
    raw_ws = body.get("workspaces") or []
    workspaces_spec = raw_ws if (raw_ws and isinstance(raw_ws[0], dict)) else None
    workspace_ids = None if workspaces_spec else (raw_ws or None)

    mode = body.get("mode", "mock")
    auth_token = None
    if mode == "live":
        auth_token = auth_service.token_for(body.get("auth_session"))
        if not auth_token:
            return jsonify({"error": "Not signed in - complete OAuth sign-in first."}), 400

    try:
        res = audit_service.run_audit(
            body.get("project", current_app.config["DEFAULT_PROJECT"]), mode=mode,
            pillars=body.get("pillars") or None,
            workspace_ids=workspace_ids, workspaces_spec=workspaces_spec,
            out_dir=current_app.config["OUT_DIR"], auth_token=auth_token)
        return jsonify(audit_service.to_json(res))
    except Exception as exc:  # pragma: no cover
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500
