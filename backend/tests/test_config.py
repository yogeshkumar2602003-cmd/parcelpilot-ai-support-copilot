"""Tests for the central Settings/config module -- the fix for the
"ANTHROPIC_API_KEY is missing" bug.

No live Anthropic API call is made anywhere in this file (or anywhere in
the suite): AnthropicLLMClient only constructs the real `anthropic.Anthropic`
client lazily inside `_get_client()`, which we never call here.
"""
from __future__ import annotations

from app import config
from app.agent.llm_client import AnthropicLLMClient, NoAPIKeyError


def test_settings_detects_a_configured_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake-key-value")
    s = config.reload_settings()
    assert s.ai_configured is True
    assert s.anthropic_api_key == "sk-ant-test-fake-key-value"


def test_settings_safe_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = config.reload_settings()
    assert s.ai_configured is False
    assert s.anthropic_api_key == ""


def test_settings_strips_whitespace_around_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  sk-ant-padded  ")
    s = config.reload_settings()
    assert s.anthropic_api_key == "sk-ant-padded"


def test_settings_repr_and_str_never_leak_the_key(monkeypatch):
    secret = "sk-ant-super-secret-value-should-never-appear"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    s = config.reload_settings()
    assert secret not in repr(s)
    assert secret not in str(s)
    assert "ai_configured=True" in repr(s)


def test_settings_model_env_var_respected(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test-model-id")
    s = config.reload_settings()
    assert s.anthropic_model == "claude-test-model-id"


def test_anthropic_llm_client_reads_live_settings_dynamically(monkeypatch):
    """Regression test for the original bug's root architectural cause:
    AnthropicLLMClient used to import ANTHROPIC_API_KEY BY VALUE at module
    load time, creating a second, independently-stale copy of the setting.
    It must now read through the `config` module reference so a single
    `reload_settings()` call is immediately visible to it.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config.reload_settings()
    client = AnthropicLLMClient()
    assert client.api_key == ""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-newly-configured")
    config.reload_settings()
    client2 = AnthropicLLMClient()
    assert client2.api_key == "sk-ant-newly-configured"


def test_anthropic_llm_client_raises_clear_error_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config.reload_settings()
    client = AnthropicLLMClient()
    try:
        client._get_client()
        assert False, "expected NoAPIKeyError"
    except NoAPIKeyError as e:
        assert "ANTHROPIC_API_KEY" in str(e)


def test_no_api_key_error_message_never_contains_a_key_value(monkeypatch):
    # Even if a bogus/garbage "key" were somehow passed in, the raised
    # error's message is a fixed string that never interpolates it.
    client = AnthropicLLMClient(api_key="")
    try:
        client._get_client()
        assert False, "expected NoAPIKeyError"
    except NoAPIKeyError as e:
        assert "sk-ant" not in str(e)


def test_env_file_discovery_uses_absolute_paths_independent_of_cwd():
    """The root cause of the original bug was `load_dotenv()` resolving its
    target file inconsistently depending on how/where the process was
    launched. Prove the fix: both candidate paths are absolute and derived
    from this module's own file location, never from the process cwd."""
    candidates = config.dotenv_candidates()
    assert len(candidates) == 2
    for path in candidates:
        assert path.is_absolute()
    assert candidates[0] == config.PROJECT_ROOT / ".env"
    assert candidates[1] == config.BACKEND_DIR / ".env"
    # PROJECT_ROOT/BACKEND_DIR are computed once from Path(__file__).resolve()
    # at import time -- changing the process's cwd afterwards must not move them.
    import os
    original_cwd = os.getcwd()
    try:
        os.chdir(config.PROJECT_ROOT.parent if config.PROJECT_ROOT.parent.exists() else config.PROJECT_ROOT)
        assert config.dotenv_candidates() == candidates
    finally:
        os.chdir(original_cwd)


def test_load_env_files_populates_settings_from_an_explicit_path(tmp_path, monkeypatch):
    """Directly exercises the loading mechanism (not the real project .env)
    with a temporary file at an arbitrary absolute path, proving
    `load_env_files` correctly loads whatever absolute path it's given."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env_file = tmp_path / "some_dir" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("ANTHROPIC_API_KEY=sk-ant-from-explicit-file\n")

    config.load_env_files([env_file])
    try:
        s = config.reload_settings()
        assert s.anthropic_api_key == "sk-ant-from-explicit-file"
    finally:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        config.reload_settings()


def test_load_env_files_does_not_override_real_env_vars(tmp_path, monkeypatch):
    """A real, already-exported environment variable (e.g. set by a
    container platform) must win over a value in a .env file."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-real-environment")
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-ant-from-dotenv-file\n")

    config.load_env_files([env_file])
    s = config.reload_settings()
    assert s.anthropic_api_key == "sk-ant-from-real-environment"


def test_load_env_files_ignores_missing_files(tmp_path):
    missing = tmp_path / "does_not_exist" / ".env"
    assert not missing.exists()
    config.load_env_files([missing])  # must not raise
