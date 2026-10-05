"""导出层回归测试。

重点：
1. 标题只放在文件名里，正文不重复写标题（用户的归档规则）
2. frontmatter 含来源信息（公众号名、主体、原文 URL）
3. 文件名里的非法字符被替换
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wxmp.config import Config  # noqa: E402
from wxmp.export import export_articles, sanitize_filename  # noqa: E402
from wxmp.store import ArticleRow, Store, canonical_url  # noqa: E402

URL = (
    "http://mp.weixin.qq.com/s?__biz=MzIzMTEzMzMxMA==&mid=2247517885&idx=1"
    "&sn=4d028514086862299586aa1039697a0a&chksm=abc"
)


def test_文件名非法字符被替换():
    assert sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert sanitize_filename("   ") == "untitled"
    assert sanitize_filename("正常标题 2026") == "正常标题 2026"
    assert sanitize_filename("") == "untitled"


def test_文件名超长被截断():
    assert len(sanitize_filename("啊" * 300)) <= 120


def _setup(tmp: Path):
    store = Store(tmp / "wxmp.db")
    store.upsert_account(
        username="mtlsnow",
        nick="仓都加满",
        user_name="gh_e2899e9a812e",
        media_name="深圳和光同行传媒有限公司",
        added_at="2026-10-02T12:00:00+08:00",
    )
    store.insert_article(
        ArticleRow(
            url=canonical_url(URL),
            account="mtlsnow",
            title="今天港股为什么开盘大跌？",
            digest="买方深度骤降 70%",
            content="出去散步回来，突然发现港股开盘大跌，恒生跌幅2.5%。",
            published=1790909162,
            collected=1790911038,
        )
    )
    cfg = Config()
    cfg.export.enabled = True
    cfg.export.dir = str(tmp / "export")
    cfg.export.layout = "by_account"
    return store, cfg


def test_导出生成正确路径与内容():
    tmp = Path(tempfile.mkdtemp(prefix="wxmp-export-"))
    store, cfg = _setup(tmp)
    try:
        written = export_articles(store, cfg)
        assert len(written) == 1
        p = written[0]
        # 按账号建子目录
        assert p.parent.name == "仓都加满"
        # 文件名以发布日期开头
        assert p.name.startswith("2026-10-02")
        assert "今天港股为什么开盘大跌？" in p.name

        text = p.read_text(encoding="utf-8")
        # frontmatter 必须含来源信息
        assert "来源: 微信公众号「仓都加满」" in text
        assert "主体: 深圳和光同行传媒有限公司" in text
        assert "原文: http://mp.weixin.qq.com/s?" in text
        assert "发布: 2026-10-02 10:46" in text
        # 正文
        assert "港股开盘大跌" in text
        # 来源区块
        assert "## 来源" in text
        assert "原文链接" in text
        # ⚠️ 标题只应在文件名与 frontmatter 的"来源"里出现，
        # 不应在正文顶部再写一遍 H1
        assert not text.startswith("# ")
        assert "\n# 今天港股" not in text
    finally:
        store.close()


def test_导出后标记为已导出():
    tmp = Path(tempfile.mkdtemp(prefix="wxmp-export-"))
    store, cfg = _setup(tmp)
    try:
        assert store.stats()["exported"] == 0
        export_articles(store, cfg)
        assert store.stats()["exported"] == 1
        # 第二次不应重复导出
        assert export_articles(store, cfg) == []
        # --all 才会重导
        assert len(export_articles(store, cfg, only_unexported=False)) == 1
    finally:
        store.close()


def test_同日同标题文章不会互相覆盖(tmp_path):
    store, cfg = _setup(tmp_path)
    try:
        store.insert_article(
            ArticleRow(
                url=canonical_url(URL.replace("mid=2247517885", "mid=2247517886")),
                account="mtlsnow",
                title="今天港股为什么开盘大跌？",
                content="第二篇的正文",
                published=1790909163,
                collected=1790911039,
            )
        )

        written = export_articles(store, cfg)

        assert len(written) == len(set(written)) == 2
        assert all(path.exists() for path in written)
        assert any("第二篇的正文" in path.read_text(encoding="utf-8") for path in written)
        assert store.stats()["exported"] == 2
    finally:
        store.close()


def test_flat布局不建子目录():
    tmp = Path(tempfile.mkdtemp(prefix="wxmp-export-"))
    store, cfg = _setup(tmp)
    cfg.export.layout = "flat"
    try:
        written = export_articles(store, cfg)
        assert written[0].parent == Path(cfg.export.dir)
    finally:
        store.close()


def test_未配置导出目录时报错():
    import pytest

    tmp = Path(tempfile.mkdtemp(prefix="wxmp-export-"))
    store, cfg = _setup(tmp)
    cfg.export.dir = ""
    try:
        with pytest.raises(ValueError, match="export.dir"):
            export_articles(store, cfg)
    finally:
        store.close()


def test_不加粗时保留原文():
    tmp = Path(tempfile.mkdtemp(prefix="wxmp-export-"))
    store, cfg = _setup(tmp)
    cfg.export.bold_keywords = False
    try:
        text = export_articles(store, cfg)[0].read_text(encoding="utf-8")
        assert "**2.5**" not in text
    finally:
        store.close()
