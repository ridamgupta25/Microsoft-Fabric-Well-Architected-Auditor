"""Flask application factory.

Builds the web app that serves the separated front end (from ``frontend/``) and
exposes the JSON API used by the browser. Keeping this as a factory
(``create_app``) is the standard Flask pattern: it lets the CLI, a WSGI server
(``wsgi.py``), and tests each construct an app with their own configuration.

The API and audit logic live in :mod:`auditfast.services` and
:mod:`auditfast.core`; the routes here are thin adapters.
"""
from __future__ import annotations

import os

from flask import Flask, send_from_directory
from flask_cors import CORS

# Path to the separated front end (auditfast-core/frontend), resolved relative
# to this file: web -> auditfast -> backend -> auditfast-core -> frontend.
_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "..", "frontend"))


def create_app(default_project: str, out_dir: str | None = None) -> Flask:
    """Create and configure the Flask app.

    Args:
        default_project: path to the project YAML the UI opens with.
        out_dir: where generated reports are written (defaults to ./output).
    """
    out_dir = out_dir or os.path.abspath("output")
    os.makedirs(out_dir, exist_ok=True)

    # static_folder serves the front-end assets under /static/... ; the SPA
    # entry point (index.html) is served from "/" below.
    app = Flask(__name__, static_folder=_FRONTEND_DIR, static_url_path="/static")
    app.config.update(
        DEFAULT_PROJECT=default_project,
        OUT_DIR=out_dir,
        FRONTEND_DIR=_FRONTEND_DIR,
    )

    # Allow a separately-hosted front end to call the API cross-origin.
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    @app.get("/")
    def index():  # pragma: no cover - trivial
        """Serve the single-page front end."""
        return send_from_directory(_FRONTEND_DIR, "index.html")

    # Register the API blueprints.
    from .routes.audit_routes import bp as audit_bp
    from .routes.auth_routes import bp as auth_bp
    from .routes.config_routes import bp as config_bp
    from .routes.download_routes import bp as download_bp
    from .routes.workspace_routes import bp as workspace_bp

    for blueprint in (config_bp, auth_bp, workspace_bp, audit_bp, download_bp):
        app.register_blueprint(blueprint)

    return app
