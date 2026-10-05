"""把库里的文章导出成 Markdown。

几个刻意的决定：

1. **正文用 content_text（纯文本），不转 HTML**。
   HTML 里的图在 `data-src` 而非 `src`（微信懒加载），且 URL 里的 & 被转义成 &amp;。
   标准转换器直接吃这段 HTML 一张图都转不出来 —— 要图必须自己写一步提取。
   而 content_text 干净，代价是丢失原文的加粗/颜色。
   v0.1 先保证纯文本链路可靠，原文 HTML 已进 raw 存档，将来想加图不用重抓。

2. **文件名用发布日期**，符合直觉；采集时刻写进 frontmatter。

3. **不加 H1 标题**。用户的归档规则明确要求"标题只放在文件名里，正文不重复写标题"。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
from pathlib import Path

from .config import Config
from .store import Store, url_id

log = logging.getLogger(__name__)

# 文件名里不能出现的字符（POSIX 只禁 / 和 NUL，但 Windows 共享目录还要防这些）
_BAD_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# 常见 A 股 / 港股代码形态，用于加粗关键词
_CODE_RE = re.compile(r"\b(?:[036]\d{5}|[0-9]{5})\b")


def sanitize_filename(name: str, max_len: int = 120) -> str:
    cleaned = _BAD_FILENAME.sub("_", name).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned or "untitled"


def fmt_date(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def fmt_datetime(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def bold_keywords(text: str, account_keywords: list[str] | None = None) -> str:
    """给股票代码加粗。

    刻意保持保守：只加粗可确定的代码形态，不做实体识别。
    乱加粗会破坏原文可读性，比不加更糟。
    """
    if not text:
        return text
    return _CODE_RE.sub(lambda m: f"**{m.group(0)}**", text)


def render_markdown(row, cfg: Config, account_meta: dict | None = None) -> str:
    meta = account_meta or {}
    nick = meta.get("nick") or row["account"]
    media = meta.get("media_name") or ""
    user_name = meta.get("user_name") or ""

    body = row["content"] or ""
    if cfg.export.bold_keywords:
        body = bold_keywords(body)

    lines: list[str] = ["---"]
    lines.append(f"来源: 微信公众号「{nick}」")
    if media:
        lines.append(f"主体: {media}")
    lines.append(f"原文: {row['url']}")
    lines.append(f"发布: {fmt_datetime(row['published'])}")
    lines.append(f"采集: {fmt_datetime(row['collected'])}")
    lines.append(f"账号: {row['account']}")
    lines.append("---")
    lines.append("")
    lines.append(body.strip())
    lines.append("")
    lines.append("## 来源")
    lines.append(f"- 公众号：{nick}（{row['account']}）")
    if media:
        lines.append(f"- 主体：{media}")
    lines.append(f"- 原文链接：{row['url']}")
    if user_name:
        lines.append(f"- 账号标识：{user_name}")
    lines.append("")
    return "\n".join(lines)


def export_articles(
    store: Store,
    cfg: Config,
    since: int | None = None,
    account: str | None = None,
    only_unexported: bool = True,
) -> list[Path]:
    """导出文章为 Markdown 文件，返回写出的路径列表。"""
    if not cfg.export.dir:
        raise ValueError(
            "export.dir 未配置 —— 请先在 config.json 里指定 Markdown 输出目录"
        )

    rows = store.unexported(since) if only_unexported else store.recent(account, limit=10000)
    written: list[Path] = []
    done_urls: list[str] = []
    meta_cache: dict[str, dict] = {}

    for row in rows:
        acc = row["account"]
        if account and acc != account:
            continue
        if acc not in meta_cache:
            a = next((x for x in store.list_accounts() if x["username"] == acc), None)
            meta_cache[acc] = dict(a) if a else {}
        meta = meta_cache[acc]
        nick = meta.get("nick") or acc

        base = Path(cfg.export.dir).expanduser()
        out_dir = base / sanitize_filename(nick) if cfg.export.layout == "by_account" else base
        out_dir.mkdir(parents=True, exist_ok=True)

        fname = cfg.export.filename.format(
            date=fmt_date(row["published"]),
            title=sanitize_filename(row["title"] or url_id(row["url"])),
            account=sanitize_filename(nick),
            id=url_id(row["url"]),
        )
        name = Path(fname)
        identity = hashlib.sha256(row["url"].encode("utf-8")).hexdigest()[:12]
        fname = str(name.with_name(f"{name.stem} [{identity}]{name.suffix}"))
        target = out_dir / fname

        # 原子写：先写同目录临时文件再 replace，避免半截文件
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(render_markdown(row, cfg, meta), encoding="utf-8")
        tmp.replace(target)

        written.append(target)
        done_urls.append(row["url"])

    # 只有真正写成功的才标记已导出
    store.mark_exported(done_urls)
    return written
