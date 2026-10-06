from pathlib import Path

from infohub.config import Config


def test_default_config_contains_sources_and_thinking_flag():
    cfg = Config.default()
    assert set(cfg.sources) == {"wxmp", "xnews"}
    assert cfg.llm.extra_params == {"chat_template_kwargs": {"enable_thinking": False}}
    assert cfg.llm.prompt_version == "summary-v1"
    assert "{body}" in cfg.llm.get_user_template()
    assert cfg.llm.base_url.endswith("/v1")


def test_save_load_and_permissions(tmp_path: Path):
    path = tmp_path / "config.json"
    cfg = Config.default()
    cfg.save(path)
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    loaded = Config.load(path)
    assert loaded.llm.model == cfg.llm.model
    assert loaded.llm.get_system_prompt() == cfg.llm.get_system_prompt()
    assert loaded.llm.get_user_template() == cfg.llm.get_user_template()
    assert loaded.sources["wxmp"].database == cfg.sources["wxmp"].database


def test_custom_prompts_round_trip(tmp_path: Path):
    """Prompts are now stored in code, not config JSON."""
    path = tmp_path / "config.json"
    cfg = Config.default()
    cfg.llm.prompt_version = "summary-v1"  # Only version is stored
    cfg.save(path)
    loaded = Config.load(path)
    assert loaded.llm.prompt_version == "summary-v1"
    # Prompts come from DEFAULT_PROMPTS constant
    assert loaded.llm.get_system_prompt() == cfg.llm.get_system_prompt()
    assert loaded.llm.get_user_template() == cfg.llm.get_user_template()


def test_credentials_only_read_from_named_environment(monkeypatch):
    cfg = Config.default()
    monkeypatch.setenv("LLM_API_KEY", "key-from-env")
    monkeypatch.setenv("MEMOS_TOKEN", "memos-from-env")
    assert cfg.llm_api_key() == "key-from-env"
    assert cfg.memos_token() == "memos-from-env"


def test_service_urls_can_be_overridden(monkeypatch):
    cfg = Config.default()
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.local/v1/")
    monkeypatch.setenv("MEMOS_URL", "http://memos.local/")
    assert cfg.llm.effective_base_url() == "http://llm.local/v1"
    assert cfg.memos.effective_base_url() == "http://memos.local"
