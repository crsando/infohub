from pathlib import Path

import pytest

from xnews.config import Account, Config
from xnews.errors import ConfigError


def test_token_environment_has_priority(monkeypatch):
    cfg = Config()
    cfg.provider.token = "config-token"
    monkeypatch.setenv("TIKHUB_TOKEN", " env-token ")
    assert cfg.effective_token() == "env-token"
    assert "TIKHUB_TOKEN" in cfg.token_source()


def test_blank_environment_falls_back(monkeypatch):
    cfg = Config()
    cfg.provider.token = "config-token"
    monkeypatch.setenv("TIKHUB_TOKEN", "  ")
    assert cfg.effective_token() == "config-token"


def test_config_roundtrip_and_atomic_permissions(tmp_path):
    cfg = Config(accounts=[Account(key="macro", screen_name="MacroMargin", rest_id="1")])
    path = tmp_path / "config.json"
    cfg.save(path)
    back = Config.load(path)
    assert back.accounts[0].rest_id == "1"
    assert path.stat().st_mode & 0o777 == 0o600


def test_account_duplicates_are_rejected():
    cfg = Config(accounts=[Account(key="macro", screen_name="MacroMargin", rest_id="1")])
    with pytest.raises(ConfigError):
        cfg.add_account(Account(key="other", screen_name="macromargin"))
    with pytest.raises(ConfigError):
        cfg.add_account(Account(key="other", screen_name="Other", rest_id="1"))


def test_config_requires_identity():
    with pytest.raises(ConfigError):
        Config(accounts=[Account(key="empty")]).validate()
