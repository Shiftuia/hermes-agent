"""Codex fallback must not inherit an unrelated model's entitlement bench."""

import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.auxiliary_client import resolve_provider_client


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # A source checkout inside the real Hermes home is not a sealed PM payload.
    monkeypatch.setattr("pm.environments._payload_manifest", lambda root: None)
    entry = {
        "id": "barbara-test", "label": "barbara-test", "auth_type": "oauth",
        "priority": 0, "source": "manual:device_code", "access_token": "test-oauth-token",
        "refresh_token": "test-refresh", "base_url": "https://chatgpt.com/backend-api/codex",
        "model_cooldowns": {"unavailable-model": time.time() + 86400},
    }
    (tmp_path / "auth.json").write_text(json.dumps({
        "version": 1, "credential_pool": {"openai-codex": [entry]},
    }))
    return tmp_path, entry


@pytest.mark.parametrize("raw_codex", [True, False])
def test_codex_route_selects_pool_for_requested_model(codex_home, raw_codex):
    tmp_path, entry = codex_home

    client, model = resolve_provider_client("openai-codex", model="gpt-6.1-sol", raw_codex=raw_codex)
    assert client is not None
    assert model == "gpt-6.1-sol"

    blocked, _ = resolve_provider_client("openai-codex", model="unavailable-model", raw_codex=raw_codex)
    assert blocked is None
    saved = json.loads((tmp_path / "auth.json").read_text())
    assert saved["credential_pool"]["openai-codex"][0]["model_cooldowns"] == entry["model_cooldowns"]


def test_primary_failure_automatically_binds_codex_pool(codex_home, monkeypatch):
    import httpx
    from openai import AuthenticationError
    from run_agent import AIAgent

    with patch("model_tools.get_tool_definitions", return_value=[]):
        agent = AIAgent(
            api_key="test-primary", provider="anthropic", model="claude-opus-5-5",
            base_url="https://api.anthropic.com", api_mode="anthropic_messages",
            quiet_mode=True, skip_context_files=True, skip_memory=True,
            fallback_model={"provider": "openai-codex", "model": "gpt-6.1-sol"},
        )
    agent._disable_streaming = True
    agent._credential_pool = None
    monkeypatch.setattr(agent, "_try_refresh_anthropic_client_credentials", lambda: False)
    attempts = []

    def request(kwargs):
        attempts.append((agent.provider, agent.model))
        if agent.provider == "anthropic":
            raise AuthenticationError("Invalid test credential", response=httpx.Response(
                401, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")), body={})
        return SimpleNamespace(
            output=[SimpleNamespace(type="message", role="assistant", content=[
                SimpleNamespace(type="output_text", text="OK")])],
            output_text="OK", status="completed", usage=None,
        )

    monkeypatch.setattr(agent, "_interruptible_api_call", request)
    monkeypatch.setattr(agent, "_persist_session", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "_save_trajectory", lambda *a, **kw: None)
    result = agent.run_conversation("Reply OK.")
    assert result["final_response"] == "OK"
    assert attempts == [("anthropic", "claude-opus-5-5"), ("openai-codex", "gpt-6.1-sol")]
    assert agent._credential_pool_entry_id == "barbara-test"
