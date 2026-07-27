"""Shared test fixtures.

No ``sys.path`` manipulation: the package is installed (``pip install -e backend``)
so tests import it the same way production does.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auditfast.config.settings import Settings
from auditfast.core.enums import Layer
from auditfast.main import create_app

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_FILE = BACKEND_ROOT / "config" / "project.example.yaml"
TENANT_FIXTURE = BACKEND_ROOT / "sample_data" / "tenant.json"

#: The mock tenant's workspaces and the layer each plays.
MOCK_TARGETS = [
    ("ws-prep-01", Layer.PREP),
    ("ws-store-01", Layer.STORAGE),
    ("ws-ops-01", Layer.OPERATIONS),
]

#: Conventions the example project enforces. Mirrors project.example.yaml, kept
#: here so engine tests do not need to parse YAML.
MOCK_SETTINGS = {
    "naming_convention": r"^[A-Za-z]+-(Dev|Test|Prod)-[A-Za-z]+$",
    "pipeline_naming_convention": r"^PL_[A-Za-z0-9_]+$",
    "orphan_days": 90,
    "max_admins": 2,
}

#: The overall score the mock tenant must produce. Pinned to the value the
#: pre-refactor implementation returned (110/190*3... i.e. 110 of 190 points), so
#: any change to a check, a band, or the roll-up fails loudly here.
EXPECTED_OVERALL = 57.89473684210527
EXPECTED_SCORED_CHECKS = 57
EXPECTED_RESULT_ROWS = 60


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
def client(settings: Settings) -> TestClient:
    """A TestClient whose lifespan has run, so app.state is populated."""
    app = create_app(settings)
    # Override the cached settings dependency so routes see the temp output dir.
    from auditfast.api import deps

    app.dependency_overrides[deps.settings_dep] = lambda: settings
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def provider():
    from auditfast.clients import MockProvider

    return MockProvider(TENANT_FIXTURE)
