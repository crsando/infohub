"""RSS 2.0 feed 生成"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

if TYPE_CHECKING:
    from .config import FeedConfig


def _format_rfc822(dt: datetime) -> str:
    """格式化为 RFC 822 日期（RSS 2.0 pubDate 格式）"""
    # RSS 2.0 要求 RFC 822，例如 "Mon, 06 Sep 2021 00:01:00 +0800"
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")


def _text_to_html(content: str) -> str:
    """纯文本转 HTML，按空行切段

    注意：不需要手动转义，ElementTree 会自动处理 XML 特殊字符
    """
    if not content:
        return ""

    paragraphs = []
    for para in content.split("\n\n"):
        para = para.strip()
        if para:
            # 不转义，让 ElementTree 自动处理
            # 段内换行替换为空格
            text = para.replace("\n", " ")
            paragraphs.append(f"<p>{text}</p>")

    return "\n".join(paragraphs)


def render_feed(
    articles: list[dict],
    accounts: dict[str, dict],
    config: FeedConfig,
    account_filter: str | None = None,
) -> bytes:
    """生成 RSS 2.0 feed XML

    Args:
        articles: 文章列表，每项包含 url, title, digest, content, published, account
        accounts: 账号字典，key 为 username，value 包含 nick 和 media_name
        config: Feed 配置
        account_filter: 可选，只包含指定账号的文章（用于 per_account 模式）

    Returns:
        UTF-8 编码的 XML 字节串
    """
    # 过滤和排序
    filtered = articles
    if account_filter:
        filtered = [a for a in filtered if a.get("account") == account_filter]

    # 按发布时间倒序，取前 N 篇
    filtered = sorted(filtered, key=lambda x: x.get("published") or 0, reverse=True)
    filtered = filtered[: config.max_items]

    # 构建 XML
    rss = ET.Element("rss", version="2.0")
    rss.set("xmlns:content", "http://purl.org/rss/1.0/modules/content/")

    channel = ET.SubElement(rss, "channel")

    # Channel 元数据
    title = config.title
    if account_filter and account_filter in accounts:
        account_info = accounts[account_filter]
        title = f"{config.title} - {account_info.get('nick') or account_filter}"

    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = config.link or "https://mp.weixin.qq.com"
    ET.SubElement(channel, "description").text = config.description or "微信公众号文章订阅"
    ET.SubElement(channel, "language").text = "zh-CN"
    ET.SubElement(channel, "lastBuildDate").text = _format_rfc822(datetime.now().astimezone())

    # 文章条目
    for article in filtered:
        item = ET.SubElement(channel, "item")

        # 基本字段
        ET.SubElement(item, "title").text = article.get("title") or "（无标题）"
        ET.SubElement(item, "link").text = article.get("url") or ""
        ET.SubElement(item, "guid", isPermaLink="true").text = article.get("url") or ""

        # 发布时间
        published = article.get("published")
        if published:
            try:
                dt = datetime.fromtimestamp(published).astimezone()
                ET.SubElement(item, "pubDate").text = _format_rfc822(dt)
            except (ValueError, OSError):
                pass

        # 作者和分类（公众号昵称）
        account_username = article.get("account") or ""
        if account_username in accounts:
            account_info = accounts[account_username]
            author_name = account_info.get("nick") or account_username
            ET.SubElement(item, "author").text = author_name
            ET.SubElement(item, "category").text = author_name

        # 摘要
        digest = article.get("digest") or ""
        if digest:
            ET.SubElement(item, "description").text = digest

        # 全文（纯文本转 HTML）
        content = article.get("content") or ""
        if content:
            content_html = _text_to_html(content)
            content_encoded = ET.SubElement(item, "{http://purl.org/rss/1.0/modules/content/}encoded")
            content_encoded.text = content_html

    # 生成 XML 字符串
    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")

    # 写入字节流
    import io
    buf = io.BytesIO()
    buf.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
    tree.write(buf, encoding="utf-8", xml_declaration=False)

    return buf.getvalue()


def write_feed(
    articles: list[dict],
    accounts: dict[str, dict],
    config: FeedConfig,
) -> list[Path]:
    """写出 RSS feed 文件

    Args:
        articles: 文章列表
        accounts: 账号字典
        config: Feed 配置

    Returns:
        已写入的文件路径列表
    """
    if not config.path:
        raise ValueError("feed.path 未配置")

    written = []

    # 主 feed（所有文章）
    main_path = Path(config.path).expanduser().resolve()
    xml_bytes = render_feed(articles, accounts, config)
    _atomic_write(main_path, xml_bytes)
    written.append(main_path)

    # 按账号分组（可选）
    if config.per_account:
        for username in accounts:
            account_articles = [a for a in articles if a.get("account") == username]
            if not account_articles:
                continue

            # feed-<username>.xml
            account_path = main_path.parent / f"{main_path.stem}-{username}{main_path.suffix}"
            xml_bytes = render_feed(articles, accounts, config, account_filter=username)
            _atomic_write(account_path, xml_bytes)
            written.append(account_path)

    return written


def _atomic_write(path: Path, data: bytes) -> None:
    """原子写入文件（先写临时文件再 rename）"""
    path.parent.mkdir(parents=True, exist_ok=True)

    # 写临时文件
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with open(fd, "wb") as f:
            f.write(data)
            f.flush()
            # fsync 确保写入磁盘
            import os
            os.fsync(f.fileno())

        # 设置权限为 0644（所有人可读，只有用户可写）
        Path(tmp_path).chmod(0o644)

        # 原子替换
        Path(tmp_path).rename(path)
    except Exception:
        # 清理临时文件
        try:
            Path(tmp_path).unlink()
        except FileNotFoundError:
            pass
        raise
