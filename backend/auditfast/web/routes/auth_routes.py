"""POST /api/auth/* - read-only sign-in endpoints (thin wrappers over auth_service)."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ...services import audit_service, auth_service
from ...services.auth_service import AuthError

bp = Blueprint("auth", __name__)


def _project_auth_cfg() -> dict:
    """Load the auth defaults (scopes, tenant/client ids) from the project file."""
    try:
        proj, _s, _b, _p = audit_service.load_project(current_app.config["DEFAULT_PROJECT"])
        return proj.get("auth", {}) or {}
    except Exception:
        return {}


@bp.post("/api/auth/login")
def login():
    """Email-first interactive sign-in (opens the Microsoft login in a browser)."""
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(auth_service.login_interactive(
            (body.get("email") or "").strip(),
            body.get("tenant_id"), body.get("client_id"), _project_auth_cfg()))
    except AuthError as exc:
        return jsonify({"error": exc.message}), exc.status


@bp.post("/api/auth/azcli")
def azcli():
    """Reuse an existing 'az login' session."""
    try:
        return jsonify(auth_service.login_azcli())
    except AuthError as exc:
        return jsonify({"error": exc.message}), exc.status


@bp.post("/api/auth/start")
def start():
    """Begin a device-code sign-in."""
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(auth_service.start_device_flow(
            body.get("tenant_id"), body.get("client_id"), body.get("scopes")))
    except AuthError as exc:
        return jsonify({"error": exc.message}), exc.status


@bp.post("/api/auth/poll")
def poll():
    """Poll a sign-in session for completion."""
    body = request.get_json(silent=True) or {}
    return jsonify(auth_service.poll(body.get("session")))
