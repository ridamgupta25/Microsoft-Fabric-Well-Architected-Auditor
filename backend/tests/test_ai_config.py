"""Per-request AI key (AiConfig) threading tests.

Verifies the user-supplied key turns AI on for a single call, is masked, never
leaks, and flows into Node 4 code generation — without any real model.
"""
from __future__ import annotations

from auditfast.ai.agents import code_gen_agent
from auditfast.ai.custom_runtime.base_check import clear_custom_registry
from auditfast.ai.orchestrator import is_enabled
from auditfast.ai.orchestrator.ai_config import AiConfig
from auditfast.ai.orchestrator.state import (
    CustomCheck,
    CustomCheckSession,
    LifecycleStatus,
    make_check_id,
)
from auditfast.services import custom_checks_service

import pytest

_OPENAI = AiConfig(provider="openai", api_key="sk-secret", model="gpt-x", base_url="http://x/v1")
_AZURE = AiConfig(provider="azure", api_key="k", deployment="dep", endpoint="https://a.openai.azure.com")

_GOOD = (
    "class Chk(BaseAuditCheck):\n"
    "    check_id = 'chk_gen'\n"
    "    def evaluate(self, kb):\n"
    "        return {'status': 'PASS', 'score': 100.0, 'findings': [], 'recommendations': []}\n"
)


@pytest.fixture(autouse=True)
def _clean():
    clear_custom_registry()
    yield
    clear_custom_registry()


# -- AiConfig ------------------------------------------------------------------

def test_is_configured():
    assert _OPENAI.is_configured() is True
    assert _AZURE.is_configured() is True
    assert AiConfig("openai", "", "m", "http://x").is_configured() is False  # no key
    assert AiConfig("openai", "k", "", "http://x").is_configured() is False  # no model
    assert AiConfig("azure", "k", "m").is_configured() is False              # no endpoint/deploy


def test_api_key_is_never_in_repr_or_redacted_view():
    assert "sk-secret" not in repr(_OPENAI)
    assert _OPENAI.redacted()["api_key"] == "***"
    assert "sk-secret" not in str(_OPENAI.redacted())


# -- is_enabled precedence -----------------------------------------------------

def test_is_enabled_uses_config_when_supplied():
    assert is_enabled(_OPENAI) is True
    assert is_enabled(AiConfig("openai", "", "m", "http://x")) is False


def test_is_enabled_falls_back_to_settings_when_no_config():
    # settings.ai_enabled defaults to False in tests.
    assert is_enabled(None) is False


# -- the key flows into Node 4 -------------------------------------------------

def _eligible() -> tuple[CustomCheck, CustomCheckSession]:
    check = CustomCheck(check_id=make_check_id("p"), raw_prompt="ensure something")
    check.lifecycle_status = LifecycleStatus.PROCESSED_CUSTOM
    return check, CustomCheckSession()


def test_generate_uses_the_user_key(monkeypatch):
    # A fake completion that only "works" when a per-request config is passed.
    monkeypatch.setattr(
        code_gen_agent, "complete",
        lambda *a, ai=None, **k: _GOOD if ai is not None else None,
    )
    check, session = _eligible()
    code_gen_agent.generate(check, session, ai=_OPENAI, reviewer=None)
    assert check.code_gen.status == "GENERATED"
    assert check.generated_code == _GOOD.strip()


def test_generate_without_key_is_ai_required(monkeypatch):
    monkeypatch.setattr(code_gen_agent, "complete", lambda *a, ai=None, **k: None)
    check, session = _eligible()
    code_gen_agent.generate(check, session, ai=None, reviewer=None)
    assert check.lifecycle_status is LifecycleStatus.AI_REQUIRED


# -- verify_ai -----------------------------------------------------------------

def test_verify_ai_ok(monkeypatch):
    monkeypatch.setattr(custom_checks_service, "diagnose", lambda *a, **k: ("ok", None))
    result = custom_checks_service.verify_ai(_OPENAI)
    assert result["ok"] is True


def test_verify_ai_incomplete_config():
    result = custom_checks_service.verify_ai(AiConfig("openai", "", "", None))
    assert result["ok"] is False


def test_verify_ai_unreachable(monkeypatch):
    monkeypatch.setattr(
        custom_checks_service, "diagnose", lambda *a, **k: (None, "connection refused")
    )
    result = custom_checks_service.verify_ai(_OPENAI)
    assert result["ok"] is False
