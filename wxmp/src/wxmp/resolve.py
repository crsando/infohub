"""把用户给的"名称"或"文章 URL"解析成可拉取的账号标识。

这是整个工具最容易出错的一步 —— 实测搜「仓都加满」返回 14 条候选，里面有：
  - 正品：仓都加满 / mtlsnow / 深圳和光同行传媒有限公司
  - 李鬼：仓满加仓 / cangmanjiacang / 个人
  - 撇清关系的：仓都加满不要怂（简介里写明"本号与公众号'仓都加满'无关"）
  - 同名视频号、以及各种名字里碰巧含"仓都"的号

**猜错的代价是静默订阅到错误的号**，比直接失败糟糕得多。
所以这里的策略是：能唯一确定就自动完成，一旦有歧义就必须让人确认。
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

from .api import TikHubClient
from .errors import UsageError

# 文章 URL 里的 __biz：base64 形态（MzA...）或数字形态
_BIZ_RE = re.compile(r"__biz=([A-Za-z0-9+/=]+)")


@dataclass
class Candidate:
    nick: str
    username: str
    user_name: str
    media_name: str
    desc: str

    def render(self, index: int) -> str:
        who = self.media_name or "个人/未知主体"
        line = f"  [{index}] {self.nick}"
        if self.username:
            line += f"  ({self.username})"
        line += f"\n      主体: {who}"
        if self.desc:
            line += f"\n      简介: {self.desc[:60]}"
        return line


def looks_like_url(text: str) -> bool:
    return text.startswith(("http://", "https://")) or "mp.weixin.qq.com" in text


def parse_biz_from_url(url: str) -> str | None:
    """从文章 URL 里抽出 __biz 参数。

    __biz 是公众号的稳定标识之一，可以据此反查账号，
    省掉"用标题搜索再消歧"这一整步 —— 从 URL 进来的路径天然无歧义。
    """
    m = _BIZ_RE.search(url)
    return m.group(1) if m else None


def to_candidates(items: list[dict]) -> list[Candidate]:
    seen: set[str] = set()
    out: list[Candidate] = []
    for it in items:
        uname = it.get("username") or ""
        nick = it.get("nick") or ""
        # 没有 username 的候选（多为视频号或结构不同的条目）无法用于拉取，跳过
        if not uname:
            continue
        key = uname.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Candidate(
                nick=nick,
                username=uname,
                user_name=it.get("user_name") or "",
                media_name=it.get("media_name") or "",
                desc=it.get("desc") or "",
            )
        )
    return out


def pick_candidate(candidates: list[Candidate], keyword: str, prefer_media: str = "") -> Candidate | None:
    """在候选里找唯一确定的那一个。找不到唯一的就返回 None，交给上层要求人工确认。"""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # ① 昵称完全相等 且 只有一条 → 唯一确定
    exact = [c for c in candidates if c.nick.strip() == keyword.strip()]
    if len(exact) == 1:
        return exact[0]

    # ② 指定了主体公司名，且只有一条匹配 → 唯一确定
    if prefer_media:
        by_media = [c for c in candidates if prefer_media in (c.media_name or "")]
        if len(by_media) == 1:
            return by_media[0]

    # ③ 昵称完全相等有多条 → 有歧义，不猜
    return None


def resolve_by_name(
    client: TikHubClient,
    keyword: str,
    pick: int | None = None,
    prefer_media: str = "",
) -> tuple[Candidate | None, list[Candidate]]:
    """按名称解析。

    返回 (确定的结果 或 None, 全部候选)。
    调用方拿 None 时应当展示候选列表要求消歧，而不是随便选一个。
    """
    raw = client.search_accounts(keyword)
    candidates = to_candidates(raw)
    if not candidates:
        raise UsageError(
            f"搜不到公众号「{keyword}」",
            hint="换个关键词试试，或直接用 `wxmp add <username>` 指定标识",
        )
    if pick is not None:
        if not 1 <= pick <= len(candidates):
            raise UsageError(f"--pick 超出范围: {pick}（共 {len(candidates)} 个候选）")
        return candidates[pick - 1], candidates
    return pick_candidate(candidates, keyword, prefer_media), candidates


def resolve_by_url(client: TikHubClient, url: str) -> Candidate:
    """按文章 URL 解析账号。

    从 URL 直接读 __biz，再通过文章详情里的 user_name 定位账号 —— 全程无歧义。
    """
    biz = parse_biz_from_url(url)
    if not biz:
        raise UsageError(
            "URL 里找不到 __biz 参数",
            hint="请使用 mp.weixin.qq.com/s?... 形式的文章链接",
        )
    detail = client.article_detail(url)
    content = detail["content"]
    user_name = content.get("user_name") or ""
    nick = content.get("nick_name") or ""
    if not user_name:
        raise UsageError(
            "文章详情里没有公众号标识",
            hint="该文章可能已被删除或需要登录",
        )
    return Candidate(
        nick=nick,
        username=user_name,
        user_name=user_name,
        media_name="",
        desc=f"从文章 URL 解析（__biz={biz}）",
    )


def normalize_target(target: str) -> tuple[str, str]:
    """判断输入是 URL 还是名称/标识。

    返回 ("url"|"name", 值)

    这里**只去掉 fragment**，不动 query ——
    文章详情接口需要完整链接才能解析，过早裁剪参数会让上游拿不到内容。
    去重所需的归一化在落库时做（见 store.canonical_url）。
    """
    t = target.strip()
    if looks_like_url(t):
        p = urllib.parse.urlsplit(t)
        clean = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))
        return "url", clean
    return "name", t
