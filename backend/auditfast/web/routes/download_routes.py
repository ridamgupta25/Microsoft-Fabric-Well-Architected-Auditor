"""GET /api/download/<kind> - download the generated Markdown or Excel report."""
from __future__ import annotations

import os

from flask import Blueprint, current_app, jsonify, send_from_directory

bp = Blueprint("download", __name__)


@bp.get("/api/download/<kind>")
def download(kind: str):
    out_dir = current_app.config["OUT_DIR"]
    name = "audit-report.md" if kind == "md" else "audit-report.xlsx"
    if not os.path.exists(os.path.join(out_dir, name)):
        return jsonify({"error": "run an audit first"}), 404
    return send_from_directory(out_dir, name, as_attachment=True, download_name=name)
