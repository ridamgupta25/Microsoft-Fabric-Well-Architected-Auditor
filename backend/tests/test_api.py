"""API smoke tests using Flask's test client (no browser, no live server).

Run from the ``backend`` directory::

    ..\\.venv\\Scripts\\python.exe -m pytest -q

These exercise the HTTP layer end-to-end against mock data, so a developer can
test the backend on its own.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from auditfast.web import create_app  # noqa: E402

PROJECT = "config/project.example.yaml"


@pytest.fixture
def client():
    app = create_app(PROJECT)
    app.config.update(TESTING=True)
    return app.test_client()


def test_config_lists_pillars(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    assert "Security" in r.get_json()["pillars"]


def test_workspaces_mock(client):
    r = client.get("/api/workspaces?mode=mock")
    assert r.status_code == 200
    assert isinstance(r.get_json(), list)


def test_run_mock_scores(client):
    body = {"mode": "mock", "pillars": ["Security"],
            "workspaces": [{"id": "ws-prep-01", "role": "Data Prep"}]}
    r = client.post("/api/run", json=body)
    assert r.status_code == 200
    data = r.get_json()
    assert data["overall"] is not None
    assert 0 <= data["overall"] <= 100


def test_run_bad_workspace_reports_error(client):
    body = {"mode": "mock", "pillars": ["Security"],
            "workspaces": [{"id": "does-not-exist", "role": "Data Prep"}]}
    data = client.post("/api/run", json=body).get_json()
    assert data["errors"] and data["errors"][0]["workspace"] == "does-not-exist"
