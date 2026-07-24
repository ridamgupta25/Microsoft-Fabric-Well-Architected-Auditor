"""Workspace listing + connectivity diagnostics."""
from __future__ import annotations

import traceback

from flask import Blueprint, current_app, jsonify, request

from ...services import audit_service, auth_service

bp = Blueprint("workspaces", __name__)


@bp.get("/api/workspaces")
def list_workspaces():
    """List the workspaces available for selection (mock fixture or project file)."""
    project = request.args.get("project", current_app.config["DEFAULT_PROJECT"])
    mode = request.args.get("mode", "mock")
    try:
        return jsonify(audit_service.list_workspaces(project, mode))
    except Exception as exc:  # pragma: no cover
        return jsonify({"error": str(exc)}), 500


@bp.post("/api/workspaces/live")
def live_workspaces():
    """Enumerate every workspace the signed-in user can access."""
    body = request.get_json(silent=True) or {}
    token = auth_service.token_for(body.get("session"))
    if not token:
        return jsonify({"error": "Sign in first, then load your workspaces."}), 400
    try:
        return jsonify(audit_service.list_live_workspaces(token))
    except Exception as exc:  # pragma: no cover
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


@bp.post("/api/diag")
def diagnose():
    """Probe Fabric connectivity for the signed-in token."""
    body = request.get_json(silent=True) or {}
    token = auth_service.token_for(body.get("session"))
    if not token:
        return jsonify({"error": "Sign in first, then diagnose."}), 400
    try:
        return jsonify(audit_service.diagnose(token))
    except Exception as exc:  # pragma: no cover
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500
