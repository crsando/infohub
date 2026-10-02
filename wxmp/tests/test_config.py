"""配置层回归测试。

重点锁住两件事：
1. 时刻归一化（"9:00" → "09:00"）
2. **同一公众号的重复订阅防护** —— 实测踩到过：同一个号有两个合法标识
   （自定义微信号 mtlsnow 与官方 gh_e2899e9a812e），只比对 username 会漏判。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wxmp.config import Account, Config, normalize_time  # noqa: E402
from wxmp.errors import ConfigError  # noqa: E402


# ---------- 时刻归一化 ----------

def test_时刻归一化_补零():
    assert normalize_time("9:00") == "09:00"
    assert normalize_time("09:00") == "09:00"
    assert normalize_time(" 16:30 ") == "16:30"
    assert normalize_time("0:05") == "00:05"


def test_时刻归一化_拒绝非法():
    for bad in ["25:00", "12:60", "abc", "12", "", "12:5", "-1:00"]:
        with pytest.raises(ConfigError):
            normalize_time(bad)


# ---------- 账号查重 ----------

def _cfg_with(account: Account) -> Config:
    c = Config()
    c.accounts.append(account)
    return c


def test_同名不同标识的重复订阅被拦下():
    """回归测试：同一个号的两个合法标识形式，必须判为重复。"""
    cfg = _cfg_with(
        Account(nick="仓都加满", username="mtlsnow", user_name="gh_e2899e9a812e")
    )
    with pytest.raises(ConfigError, match="已订阅"):
        cfg.add_account(
            Account(nick="仓都加满", username="gh_e2899e9a812e", user_name="gh_e2899e9a812e")
        )


def test_同username大小写不同也判重():
    cfg = _cfg_with(Account(nick="仓都加满", username="mtlsnow"))
    with pytest.raises(ConfigError, match="已订阅"):
        cfg.add_account(Account(nick="别的名", username="MTLSNOW"))


def test_同昵称也判重():
    cfg = _cfg_with(Account(nick="仓都加满", username="mtlsnow"))
    with pytest.raises(ConfigError, match="已订阅"):
        cfg.add_account(Account(nick="仓都加满", username="another_id"))


def test_不同的号可以正常添加():
    cfg = _cfg_with(Account(nick="仓都加满", username="mtlsnow"))
    cfg.add_account(Account(nick="仓都加满复盘", username="yanbaose"))
    assert len(cfg.accounts) == 2


# ---------- 查找与移除 ----------

def test_find_account支持三种键():
    cfg = _cfg_with(
        Account(nick="仓都加满", username="mtlsnow", user_name="gh_e2899e9a812e")
    )
    assert cfg.find_account("mtlsnow") is not None
    assert cfg.find_account("gh_e2899e9a812e") is not None
    assert cfg.find_account("仓都加满") is not None
    assert cfg.find_account("不存在") is None


def test_remove_account():
    cfg = _cfg_with(Account(nick="仓都加满", username="mtlsnow"))
    removed = cfg.remove_account("mtlsnow")
    assert removed.username == "mtlsnow"
    assert cfg.accounts == []
    with pytest.raises(ConfigError):
        cfg.remove_account("mtlsnow")


# ---------- 时刻覆盖 ----------

def test_号级时刻覆盖全局():
    cfg = Config()
    cfg.schedule.default_times = ["09:00", "16:00"]
    a = Account(nick="x", username="x")
    assert cfg.effective_times(a) == ["09:00", "16:00"]
    a.times = ["08:30"]
    assert cfg.effective_times(a) == ["08:30"]


# ---------- 序列化往返 ----------

def test_config往返序列化不丢字段():
    cfg = Config()
    cfg.provider.token = "test-token"
    cfg.schedule.default_times = ["09:00", "16:00"]
    cfg.accounts.append(
        Account(
            nick="仓都加满",
            username="mtlsnow",
            user_name="gh_e2899e9a812e",
            media_name="深圳和光同行传媒有限公司",
            times=["08:30"],
        )
    )
    back = Config.from_dict(cfg.to_dict())
    assert back.provider.token == "test-token"
    assert back.schedule.default_times == ["09:00", "16:00"]
    a = back.accounts[0]
    assert a.username == "mtlsnow"
    assert a.media_name == "深圳和光同行传媒有限公司"
    assert a.times == ["08:30"]


def test_缺username的账号被校验拒绝():
    cfg = Config()
    cfg.accounts.append(Account(nick="没标识"))
    with pytest.raises(ConfigError, match="username"):
        Config.from_dict(cfg.to_dict())


# ---------- 从 URL 判断输入形态 ----------

def test_中文名不会被误判为username():
    from wxmp.cli import is_username_like

    # 公众号 username 一律是 ASCII
    assert is_username_like("mtlsnow") is True
    assert is_username_like("gh_e2899e9a812e") is True
    assert is_username_like("cangmanjiacang") is True
    # 中文一定是昵称
    assert is_username_like("仓都加满") is False
    assert is_username_like("仓都加满复盘") is False
    assert is_username_like("") is False
