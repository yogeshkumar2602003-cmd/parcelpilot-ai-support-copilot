from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.fake_llm import ScriptedLLMClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PARCELPILOT_DB_PATH", str(tmp_path / "api_test.db"))
    # Force a fresh singleton connection/module state per test.
    import app.db as db_module
    db_module._singleton_conn = None
    import importlib

    from app import main as main_module
    importlib.reload(main_module)
    with TestClient(main_module.app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "ai_configured" in body
    assert isinstance(body["ai_configured"], bool)


def test_health_reflects_configured_key_as_boolean_only(client, monkeypatch):
    import app.config as config_module

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-appear-in-response")
    config_module.reload_settings()
    try:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["ai_configured"] is True
        assert "sk-ant-should-never-appear-in-response" not in r.text
    finally:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        config_module.reload_settings()


def test_meta_reports_ai_configured_without_leaking_key(client, monkeypatch):
    import app.config as config_module

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-meta-endpoint-secret")
    config_module.reload_settings()
    try:
        r = client.get("/api/meta")
        assert r.status_code == 200
        assert r.json()["ai_configured"] is True
        assert "sk-ant-meta-endpoint-secret" not in r.text
    finally:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        config_module.reload_settings()


def test_chat_requires_auth_header(client):
    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 401


def test_chat_rejects_unknown_user(client):
    r = client.post("/api/chat", json={"message": "hello"}, headers={"X-Demo-User": "nope"})
    assert r.status_code == 401


def test_chat_without_api_key_returns_structured_503(client, monkeypatch):
    import app.config as config_module

    # Single point of truth: only the environment + one reload_settings()
    # call is needed now, rather than patching both app.config and
    # app.agent.llm_client's separate copies of the constant. AI_PROVIDER is
    # pinned to "anthropic" because this test is specifically about the
    # Anthropic-key-missing path -- the repo's own .env now defaults
    # AI_PROVIDER to "ollama" (see app/config.py), which needs no key at all.
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config_module.reload_settings()
    try:
        r = client.post("/api/chat", json={"message": "hello"}, headers={"X-Demo-User": "u_support_rohit"})
        assert r.status_code == 503
        body = r.json()["detail"]
        assert body["error"] == "ai_not_configured"
        assert "ANTHROPIC_API_KEY" in body["message"]
        assert "sk-ant" not in r.text  # no key value could possibly leak here anyway
    finally:
        config_module.reload_settings()


def test_chat_with_configured_key_calls_the_real_llm_path(client, monkeypatch):
    """Once a valid key is present, chat must reach the real Anthropic call
    site (not short-circuit to the 503) -- verified by patching only the
    network boundary (Anthropic SDK client), never app.config. AI_PROVIDER
    is pinned to "anthropic" -- this test targets that provider path
    specifically, regardless of this repo's own local-mode .env default."""
    import app.config as config_module

    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-would-be-a-real-key")
    config_module.reload_settings()
    try:
        import app.api.chat as chat_module
        from tests.fake_llm import ScriptedLLMClient

        script = ScriptedLLMClient([{"text": "Configured and reachable."}])
        monkeypatch.setattr(chat_module, "AnthropicLLMClient", lambda: script)

        r = client.post("/api/chat", json={"message": "hi"}, headers={"X-Demo-User": "u_support_rohit"})
        assert r.status_code == 200
        assert "Configured and reachable." in r.json()["answer"]["answer_markdown"]
    finally:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        config_module.reload_settings()


def test_chat_with_scripted_llm_end_to_end(client, monkeypatch):
    import app.config as config_module
    import app.api.chat as chat_module

    # Pinned to "anthropic" so patching chat_module.AnthropicLLMClient below
    # is actually on the code path exercised (independent of this repo's
    # own .env, which defaults AI_PROVIDER=ollama).
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    config_module.reload_settings()

    script = ScriptedLLMClient([
        {"tool_calls": [{"name": "get_order", "input": {"order_id": "ORD-3001"}}]},
        {"tool_calls": [{"name": "calculate_cancellation", "input": {"order_id": "ORD-3001"}}]},
        {"text": "ORD-3001 can be cancelled with no fee (within the 30-minute window)."},
    ])
    monkeypatch.setattr(chat_module, "AnthropicLLMClient", lambda: script)

    try:
        r = client.post(
            "/api/chat", json={"message": "Can Beacon cancel ORD-3001?"}, headers={"X-Demo-User": "u_support_rohit"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "no fee" in body["answer"]["answer_markdown"].lower()
        assert body["session_id"]
    finally:
        config_module.reload_settings()


def test_actions_endpoint_requires_matching_principal(client):
    from app.actions.pending_actions import propose_action
    from app.api.deps import get_conn
    from app.auth.principal import DEMO_USERS

    conn = get_conn()
    action = propose_action(conn, DEMO_USERS["u_support_rohit"], "create_followup_task", {"description": "x"}, "reason")

    r = client.post(f"/api/actions/{action.action_id}/confirm", headers={"X-Demo-User": "u_ops_maya"})
    assert r.status_code == 403

    r2 = client.post(f"/api/actions/{action.action_id}/confirm", headers={"X-Demo-User": "u_support_rohit"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "executed"


def test_confirm_unknown_action_404(client):
    r = client.post("/api/actions/act_doesnotexist/confirm", headers={"X-Demo-User": "u_support_rohit"})
    assert r.status_code == 404


def test_issue_radar_requires_internal_role(client):
    r = client.get("/api/issue-radar", headers={"X-Demo-User": "u_cust_northstar"})
    assert r.status_code == 403

    r2 = client.get("/api/issue-radar", headers={"X-Demo-User": "u_admin"})
    assert r2.status_code == 200
    assert "cards" in r2.json()


def test_audit_log_requires_internal_role(client):
    r = client.get("/api/audit-log", headers={"X-Demo-User": "u_cust_northstar"})
    assert r.status_code == 403


def test_accounts_endpoint_scoped_for_customer(client):
    r = client.get("/api/accounts", headers={"X-Demo-User": "u_cust_northstar"})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["account_id"] == "ACCT-001"


def test_accounts_endpoint_full_for_internal(client):
    r = client.get("/api/accounts", headers={"X-Demo-User": "u_admin"})
    assert r.status_code == 200
    assert len(r.json()) == 4
