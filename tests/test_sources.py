import sqlite3
from pathlib import Path

from infohub.config import SourceConfig
from infohub.sources import canonical_wechat_url, read_source


def test_canonical_wechat_url_removes_session_parameters():
    url = (
        "http://mp.weixin.qq.com/s?scene=126&mid=1&idx=1&sn=abc"
        "&chksm=changes-every-time&__biz=biz#rd"
    )
    assert canonical_wechat_url(url) == "http://mp.weixin.qq.com/s?__biz=biz&mid=1&idx=1&sn=abc"


def test_read_wxmp_database(tmp_path: Path):
    database = tmp_path / "wxmp.db"
    conn = sqlite3.connect(database)
    conn.executescript(
        """
        CREATE TABLE accounts(username TEXT PRIMARY KEY, nick TEXT);
        CREATE TABLE articles(url TEXT PRIMARY KEY, account TEXT, title TEXT, digest TEXT,
          content TEXT, published INTEGER, collected INTEGER, raw_path TEXT);
        INSERT INTO accounts VALUES ('demo', '测试公众号');
        INSERT INTO articles VALUES (
          'http://mp.weixin.qq.com/s?__biz=b&mid=2&idx=1&sn=s&chksm=x',
          'demo', '标题', '', '正文', 10, 20, '/tmp/raw.json'
        );
        """
    )
    conn.commit()
    conn.close()
    rows = read_source(SourceConfig("wxmp", database=str(database)), tmp_path)
    assert rows[0]["item_key"].startswith("wxmp:http://")
    assert rows[0]["author"] == "测试公众号"
    assert rows[0]["content_hash"]


def test_read_xnews_database(tmp_path: Path):
    database = tmp_path / "xnews.db"
    conn = sqlite3.connect(database)
    conn.execute(
        """CREATE TABLE posts(
          post_id TEXT PRIMARY KEY, author_screen_name TEXT, author_name TEXT,
          text TEXT, created_at INTEGER, collected_at INTEGER, url TEXT,
          raw_path TEXT, markdown_path TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO posts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("123", "macro", "Macro", "第一行\n正文", 10, 20, "https://x.com/macro/status/123", None, None),
    )
    conn.commit()
    conn.close()
    rows = read_source(SourceConfig("xnews", database=str(database)), tmp_path)
    assert rows[0]["item_key"] == "xnews:123"
    assert rows[0]["title"] == "第一行"
