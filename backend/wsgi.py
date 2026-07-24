"""WSGI entry point for a production server (gunicorn, waitress, ...).

Example (from the ``backend`` directory)::

    waitress-serve --listen=127.0.0.1:8000 wsgi:app

The project the UI opens with can be overridden via the AUDITFAST_PROJECT env var.
"""
import os

from auditfast.web import create_app

_PROJECT = os.environ.get("AUDITFAST_PROJECT", "config/project.example.yaml")

app = create_app(_PROJECT)
