"""Shared test fixtures.

No ``sys.path`` manipulation: the package is installed (``pip install -e backend``)
so tests import it the same way production does.

The application ships exactly one data source — the live Fabric provider. Tests
get their determinism from :mod:`tests.fixtures.provider`, a recorded-tenant
double that is never imported by ``auditfast.*`` itself; see that module's
docstring for why a fixture-backed provider is not the same thing as a "mock
mode" product feature.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auditfast.config.settings import Settings
from auditfast.core.enums import Layer
from auditfast.main import create_app

from .fixtures.provider import FIXTURE_FILE, RecordedProvider

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_FILE = BACKEND_ROOT / "config" / "project.example.yaml"

#: The recorded tenant's workspaces and the layer each plays.
FIXTURE_TARGETS = [
    ("ws-prep-01", Layer.PREP),
    ("ws-store-01", Layer.STORAGE),
    ("ws-ops-01", Layer.OPERATIONS),
]

#: Conventions the example project enforces. Mirrors project.example.yaml, kept
#: here so engine tests do not need to parse YAML.
FIXTURE_SETTINGS = {
    "naming_convention": r"^[A-Za-z]+-(Dev|Test|Prod)-[A-Za-z]+$",
    "pipeline_naming_convention": r"^PL_[A-Za-z0-9_]+$",
    "orphan_days": 90,
    "max_admins": 2,
}

#: The overall score the recorded tenant must produce. Pinned to the value the
#: engine returns for the fixture, so any change to a check, a band, or the
#: roll-up fails loudly here.
EXPECTED_OVERALL = 53.205128205128204
EXPECTED_SCORED_CHECKS = 104
EXPECTED_RESULT_ROWS = 243

#: A session id the auth-service patch below always resolves to a token.
#: Anything else — including a missing session — resolves to no token, so
#: unauthenticated-request tests keep working unchanged.
AUTHENTICATED_SESSION = "test-session-ok"
FAKE_TOKEN = "test-token"


@pytest.fixture(scope="session")
def project_file() -> str:
    return str(PROJECT_FILE)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointed at the example project, writing reports to a temp dir."""
    return Settings(
        default_project=str(PROJECT_FILE),
        output_dir=str(tmp_path / "output"),
        environment="test",
        log_level="WARNING",
    )


@pytest.fixture
def client(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A TestClient whose lifespan has run, so app.state is populated.

    Two things are patched so a full audit can run through the real API without
    a live Fabric tenant:

    * ``auth_service.token_for`` — only :data:`AUTHENTICATED_SESSION` resolves
      to a token, so tests exercising the unauthenticated (401) path are
      unaffected.
    * ``audit_service.build_provider`` — returns the recorded-tenant provider
      instead of constructing a real ``LiveFabricProvider``, so no network call
      is ever made.
    """
    from auditfast.api import deps
    from auditfast.services import audit_service, auth_service

    def fake_token_for(session_id: str | None) -> str | None:
        return FAKE_TOKEN if session_id == AUTHENTICATED_SESSION else None

    def fake_build_provider(config, token=None, *, refresh=False, token_refresher=None):
        return RecordedProvider(FIXTURE_FILE)

    monkeypatch.setattr(auth_service, "token_for", fake_token_for)
    monkeypatch.setattr(audit_service, "build_provider", fake_build_provider)

    app = create_app(settings)
    # Override the cached settings dependency so routes see the temp output dir.
    app.dependency_overrides[deps.settings_dep] = lambda: settings
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def provider() -> RecordedProvider:
    return RecordedProvider(FIXTURE_FILE)
