"""存储层与 URL 归一化的回归测试。

这里锁住的都是实测踩到过的坑，改代码时别把它们改回去。

运行：
    uv run --with requests --python 3.12 python -m pytest tests/ -v
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wxmp.store import ArticleRow, Store, canonical_url, url_id  # noqa: E402

# 上游两次请求返回的同一个 URL —— 除了 chksm，其余完全相同。
# 这是真实现象，不是构造出来的。
URL_A = (
    "http://mp.weixin.qq.com/s?__biz=MzIzMTEzMzMxMA=="
    "&mid=2247517798&idx=1&sn=2100276d8ba9adb73ecc14c7e2c903f7"
    "&chksm=e93e226097479918f5e9182b576f4d6911891161a09e9e10499287fa317f893022792ebd67b4"
    "&scene=126&sessionid=0#rd"
)
URL_B = (
    "http://mp.weixin.qq.com/s?__biz=MzIzMTEzMzMxMA=="
    "&mid=2247517798&idx=1&sn=2100276d8ba9adb73ecc14c7e2c903f7"
    "&chksm=e9d8e5c5a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c"
    "&scene=1&sessionid=99#rd"
)


def test_canonical_url_同一篇文章的不同chksm归一成同一个():
    a = canonical_url(URL_A)
    b = canonical_url(URL_B)
    assert a == b, f"归一化失效:\n  {a}\n  {b}"


def test_canonical_url_丢掉chksm和fragment():
    c = canonical_url(URL_A)
    assert "chksm" not in c
    assert "#" not in c
    assert "sessionid" not in c
    assert "mid=2247517798" in c
    assert "sn=2100276d8ba9adb73ecc14c7e2c903f7" in c


def test_canonical_url_不同文章仍然不同():
    other = URL_A.replace("mid=2247517798", "mid=9999999999")
    assert canonical_url(URL_A) != canonical_url(other)


def test_canonical_url_非文章链接不崩():
    # 短链形式：拿不到 __biz，应退回去掉参数的形态而不是抛异常
    assert canonical_url("https://mp.weixin.qq.com/s/AbCdEf123") == "https://mp.weixin.qq.com/s/AbCdEf123"
    assert canonical_url("") == ""


def test_url_id_人类可读形式():
    ident = url_id(URL_A)
    assert ident.startswith("2247517798_1_")


def _fresh_store() -> Store:
    d = Path(tempfile.mkdtemp(prefix="wxmp-test-"))
    return Store(d / "wxmp.db")


def _art(url: str, title: str = "测试文章") -> ArticleRow:
    return ArticleRow(
        url=url,
        account="mtlsnow",
        title=title,
        content="这是正文，提到了港股和南向资金。",
        published=1790909162,
        collected=1790911038,
    )


def test_重复插入同一url只留一行():
    """去重的核心断言：同一个 URL 插两次，库里只有一行。"""
    s = _fresh_store()
    try:
        assert s.insert_article(_art(canonical_url(URL_A))) is True
        assert s.insert_article(_art(canonical_url(URL_A))) is False
        assert s.stats()["articles"] == 1
    finally:
        s.close()


def test_不同chksm的同一篇文章去重():
    """回归测试：不归一化的话这里会变成 2 行。"""
    s = _fresh_store()
    try:
        s.insert_article(_art(canonical_url(URL_A)))
        s.insert_article(_art(canonical_url(URL_B)))
        assert s.stats()["articles"] == 1, "chksm 未被剥离，导致重复入库"
    finally:
        s.close()


def test_检索_四字词走fts5():
    s = _fresh_store()
    try:
        s.insert_article(_art(canonical_url(URL_A), "港股大跌"))
        rows = s.search("南向资金")
        assert len(rows) == 1
        assert rows[0]["title"] == "港股大跌"
    finally:
        s.close()


def test_检索_两字词走like兜底():
    """回归测试：trigram 只索引 >=3 字符，两字词必须走 LIKE 才有结果。"""
    s = _fresh_store()
    try:
        s.insert_article(_art(canonical_url(URL_A), "港股大跌"))
        rows = s.search("港股")
        assert len(rows) == 1, "两字词检索失效（trigram 不覆盖短词）"
    finally:
        s.close()


def test_检索_空关键词返回空():
    s = _fresh_store()
    try:
        s.insert_article(_art(canonical_url(URL_A)))
        assert s.search("") == []
        assert s.search("   ") == []
    finally:
        s.close()


def test_检索_引号不会破坏match语法():
    s = _fresh_store()
    try:
        s.insert_article(_art(canonical_url(URL_A)))
        s.search('"港股"')  # 不应抛 sqlite3.OperationalError
    finally:
        s.close()


def test_stats的last_run按整数解析():
    """回归测试：last_run_at 是 TEXT 列，stats 必须 CAST 回整数。"""
    s = _fresh_store()
    try:
        s.upsert_account("mtlsnow", "仓都加满")
        s.mark_run("mtlsnow", 1790911038)
        st = s.stats()
        assert isinstance(st["last_run"], int)
        assert st["last_run"] == 1790911038
    finally:
        s.close()
