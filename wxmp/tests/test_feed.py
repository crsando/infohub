"""RSS feed 生成测试"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from wxmp.config import FeedConfig
from wxmp.feed import render_feed, write_feed


def test_render_feed_basic():
    """基本 feed 生成测试"""
    articles = [
        {
            "url": "http://mp.weixin.qq.com/s?__biz=b1&mid=1&idx=1&sn=s1",
            "title": "测试文章1",
            "digest": "这是摘要1",
            "content": "第一段内容。\n\n第二段内容。",
            "published": int(datetime(2026, 1, 1, 12, 0).timestamp()),
            "account": "test_account",
        },
        {
            "url": "http://mp.weixin.qq.com/s?__biz=b1&mid=2&idx=1&sn=s2",
            "title": "测试文章2",
            "digest": "这是摘要2",
            "content": "单段内容",
            "published": int(datetime(2026, 1, 2, 12, 0).timestamp()),
            "account": "test_account",
        },
    ]

    accounts = {
        "test_account": {
            "nick": "测试公众号",
            "media_name": "测试公司",
        }
    }

    config = FeedConfig(
        enabled=True,
        path="/tmp/feed.xml",
        title="测试 Feed",
        link="https://example.com",
        description="测试描述",
        max_items=100,
        per_account=False,
    )

    xml_bytes = render_feed(articles, accounts, config)

    # 验证是 XML
    root = ET.fromstring(xml_bytes)
    assert root.tag == "rss"
    assert root.get("version") == "2.0"

    # 验证 channel
    channel = root.find("channel")
    assert channel is not None
    assert channel.find("title").text == "测试 Feed"
    assert channel.find("link").text == "https://example.com"
    assert channel.find("description").text == "测试描述"

    # 验证文章条目（应该按时间倒序）
    items = channel.findall("item")
    assert len(items) == 2

    # 第一篇应该是 2026-01-02 的文章
    assert items[0].find("title").text == "测试文章2"
    assert items[0].find("link").text == "http://mp.weixin.qq.com/s?__biz=b1&mid=2&idx=1&sn=s2"
    assert items[0].find("guid").text == "http://mp.weixin.qq.com/s?__biz=b1&mid=2&idx=1&sn=s2"
    assert items[0].find("guid").get("isPermaLink") == "true"
    assert items[0].find("author").text == "测试公众号"
    assert items[0].find("category").text == "测试公众号"
    assert items[0].find("description").text == "这是摘要2"

    # 验证 content:encoded
    content_encoded = items[0].find("{http://purl.org/rss/1.0/modules/content/}encoded")
    assert content_encoded is not None
    assert "<p>单段内容</p>" in content_encoded.text


def test_render_feed_html_escape():
    """测试 HTML 特殊字符转义"""
    articles = [
        {
            "url": "http://mp.weixin.qq.com/s?__biz=b1&mid=1&idx=1&sn=s1",
            "title": "标题 <script>alert('xss')</script>",
            "digest": "摘要 & 内容",
            "content": "第一段 <tag>。\n\n第二段 & 符号。",
            "published": int(datetime(2026, 1, 1).timestamp()),
            "account": "test",
        }
    ]

    accounts = {"test": {"nick": "测试", "media_name": ""}}
    config = FeedConfig(enabled=True, path="/tmp/feed.xml", max_items=100)

    xml_bytes = render_feed(articles, accounts, config)

    # 验证原始 XML 字符串中特殊字符已转义
    xml_str = xml_bytes.decode("utf-8")
    assert "&lt;script&gt;" in xml_str
    assert "&lt;tag&gt;" in xml_str
    assert "&amp;" in xml_str

    # ET.fromstring 会自动解码转义字符，所以解析后的文本是原始值
    root = ET.fromstring(xml_bytes)
    item = root.find("channel/item")

    # 解析后标题包含原始的 < > 字符是正常的（ET 解码了）
    title = item.find("title").text
    assert "<script>" in title  # ET 已经解码

    # 内容也是一样
    content = item.find("{http://purl.org/rss/1.0/modules/content/}encoded").text
    assert "<tag>" in content
    assert "&" in content


def test_render_feed_max_items():
    """测试最大条目数限制"""
    articles = [
        {
            "url": f"http://mp.weixin.qq.com/s?__biz=b&mid={i}&idx=1&sn=s{i}",
            "title": f"文章 {i}",
            "digest": "",
            "content": f"内容 {i}",
            "published": int(datetime(2026, 1, 1).timestamp()) + i * 3600,  # 每篇间隔1小时
            "account": "test",
        }
        for i in range(1, 151)  # 150 篇文章
    ]

    accounts = {"test": {"nick": "测试", "media_name": ""}}
    config = FeedConfig(enabled=True, path="/tmp/feed.xml", max_items=50)

    xml_bytes = render_feed(articles, accounts, config)
    root = ET.fromstring(xml_bytes)

    items = root.findall("channel/item")
    assert len(items) == 50  # 应该只有 50 篇

    # 应该是最新的 50 篇
    assert items[0].find("title").text == "文章 150"
    assert items[49].find("title").text == "文章 101"


def test_render_feed_account_filter():
    """测试按账号过滤"""
    articles = [
        {
            "url": "http://mp.weixin.qq.com/s?__biz=b1&mid=1&idx=1&sn=s1",
            "title": "账号1的文章",
            "digest": "",
            "content": "内容1",
            "published": int(datetime(2026, 1, 1).timestamp()),
            "account": "account1",
        },
        {
            "url": "http://mp.weixin.qq.com/s?__biz=b2&mid=2&idx=1&sn=s2",
            "title": "账号2的文章",
            "digest": "",
            "content": "内容2",
            "published": int(datetime(2026, 1, 2).timestamp()),
            "account": "account2",
        },
    ]

    accounts = {
        "account1": {"nick": "账号1", "media_name": ""},
        "account2": {"nick": "账号2", "media_name": ""},
    }
    config = FeedConfig(enabled=True, path="/tmp/feed.xml", max_items=100)

    # 只要 account1 的文章
    xml_bytes = render_feed(articles, accounts, config, account_filter="account1")
    root = ET.fromstring(xml_bytes)

    items = root.findall("channel/item")
    assert len(items) == 1
    assert items[0].find("title").text == "账号1的文章"


def test_write_feed_atomic(tmp_path: Path):
    """测试原子写入"""
    articles = [
        {
            "url": "http://mp.weixin.qq.com/s?__biz=b&mid=1&idx=1&sn=s",
            "title": "测试",
            "digest": "",
            "content": "内容",
            "published": int(datetime(2026, 1, 1).timestamp()),
            "account": "test",
        }
    ]

    accounts = {"test": {"nick": "测试", "media_name": ""}}

    feed_path = tmp_path / "feed.xml"
    config = FeedConfig(
        enabled=True,
        path=str(feed_path),
        max_items=100,
        per_account=False,
    )

    written = write_feed(articles, accounts, config)

    assert len(written) == 1
    assert written[0] == feed_path
    assert feed_path.exists()

    # 验证文件可读
    content = feed_path.read_bytes()
    assert b"<rss" in content
    assert b"</rss>" in content


def test_write_feed_per_account(tmp_path: Path):
    """测试按账号分组生成多个 feed"""
    articles = [
        {
            "url": "http://mp.weixin.qq.com/s?__biz=b1&mid=1&idx=1&sn=s1",
            "title": "账号1文章",
            "digest": "",
            "content": "内容1",
            "published": int(datetime(2026, 1, 1).timestamp()),
            "account": "account1",
        },
        {
            "url": "http://mp.weixin.qq.com/s?__biz=b2&mid=2&idx=1&sn=s2",
            "title": "账号2文章",
            "digest": "",
            "content": "内容2",
            "published": int(datetime(2026, 1, 2).timestamp()),
            "account": "account2",
        },
    ]

    accounts = {
        "account1": {"nick": "账号1", "media_name": ""},
        "account2": {"nick": "账号2", "media_name": ""},
    }

    feed_path = tmp_path / "feed.xml"
    config = FeedConfig(
        enabled=True,
        path=str(feed_path),
        max_items=100,
        per_account=True,
    )

    written = write_feed(articles, accounts, config)

    # 应该生成 3 个文件：主 feed + 2 个账号 feed
    assert len(written) == 3
    assert feed_path in written
    assert tmp_path / "feed-account1.xml" in written
    assert tmp_path / "feed-account2.xml" in written

    # 验证每个文件都存在且包含正确内容
    for path in written:
        assert path.exists()
        content = path.read_bytes()
        assert b"<rss" in content
