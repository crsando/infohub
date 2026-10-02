"""SQLite 存储层（含 FTS5 全文检索）。

三个刻意的设计决定，都来自实测踩过的坑：

1. **去重主键用 URL**，不用标题、不用时间。
   - 搜索接口返回的 title 带 <em class="highlight"> 高亮标签，跟列表接口对不上。
   - update_time 会在编辑后变动（实测同一篇差 29 分钟），用时间判重会重复推送。
   URL 里的 mid + idx + sn 是永久唯一的，sn 还会随内容变化 —— 正是想要的语义。

2. **中文全文检索必须用 tokenize='trigram'**。
   FTS5 默认的 unicode61 对中文是整段当一个词，搜"宁德时代"搜不出来。
   trigram 按三字符切分，中文检索才可用。**这是最容易踩的坑。**

3. **published 与 collected 分开存**。一个是微信侧发布时刻，一个是本地抓取时刻，
   实测两者差 32 分钟。将来算采集延迟、判断漏采全靠这个差。
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .errors import UpstreamError

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
  username    TEXT PRIMARY KEY,
  user_name   TEXT,
  nick        TEXT,
  media_name  TEXT,
  enabled     INTEGER NOT NULL DEFAULT 1,
  added_at    TEXT,
  last_run_at TEXT,
  last_error  TEXT
);

CREATE TABLE IF NOT EXISTS articles (
  url        TEXT PRIMARY KEY,
  account    TEXT NOT NULL,
  title      TEXT,
  digest     TEXT,
  content    TEXT,
  published  INTEGER NOT NULL,
  collected  INTEGER NOT NULL,
  raw_path   TEXT,
  exported   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_articles_pub ON articles(published DESC);
CREATE INDEX IF NOT EXISTS idx_articles_acc ON articles(account, published DESC);
CREATE INDEX IF NOT EXISTS idx_articles_exported ON articles(exported);

-- 外部内容表：索引与 articles 同步，用触发器维护。
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
  title,
  content,
  content='articles',
  content_rowid='rowid',
  tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
  INSERT INTO articles_fts(rowid, title, content) VALUES (new.rowid, new.title, new.content);
END;

CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
  INSERT INTO articles_fts(articles_fts, rowid, title, content)
    VALUES ('delete', old.rowid, old.title, old.content);
END;

CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
  INSERT INTO articles_fts(articles_fts, rowid, title, content)
    VALUES ('delete', old.rowid, old.title, old.content);
  INSERT INTO articles_fts(rowid, title, content) VALUES (new.rowid, new.title, new.content);
END;

CREATE TABLE IF NOT EXISTS runs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at INTEGER NOT NULL,
  ended_at   INTEGER,
  account    TEXT,
  new_count  INTEGER NOT NULL DEFAULT 0,
  status     TEXT,
  message    TEXT
);
"""


@dataclass
class ArticleRow:
    url: str
    account: str
    title: str
    content: str
    published: int
    collected: int
    digest: str = ""
    raw_path: str = ""


# 只有这几个参数构成文章的永久唯一标识。
# 其余（chksm / scene / sessionid / from / isappinstalled / ...）都是会话相关的噪声。
_STABLE_QUERY_KEYS = ("__biz", "mid", "idx", "sn")


def canonical_url(url: str) -> str:
    """把文章 URL 归一化成可用于去重的主键。

    ⚠️ 这一步是必须的，实测踩过的坑：
    上游每次返回的 URL 里 **chksm 参数都不一样**，直接拿完整 URL 当主键会导致
    同一篇文章每次轮询都被当成新文章重复抓取（实测两轮下来库里 20 行、实际只有 10 篇）。

    真正的唯一标识是 __biz + mid + idx + sn：
      - __biz  公众号标识
      - mid    群发消息 ID
      - idx    该次群发里的第几篇
      - sn     内容签名，内容改了它会变 —— 正是想要的语义

    这里保留这四个参数并按固定顺序重排，丢弃其余全部参数，
    同时去掉 fragment 与末尾斜杠，保证同一篇文章永远得到同一个字符串。
    """
    import urllib.parse

    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip()

    q = urllib.parse.parse_qs(parts.query, keep_blank_values=False)
    picked = [(k, q[k][0]) for k in _STABLE_QUERY_KEYS if k in q and q[k]]

    if not picked:
        # 不是标准文章链接（比如 /s/<id> 短链），退回原样但去掉噪声参数
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    query = urllib.parse.urlencode(picked)
    path = parts.path.rstrip("/") or "/s"
    return urllib.parse.urlunsplit((parts.scheme or "https", parts.netloc, path, query, ""))


def url_id(url: str) -> str:
    """人类可读的短标识（mid_idx_sn），用于文件名与日志。"""
    import urllib.parse

    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    except Exception:
        return url[-40:]
    mid = (q.get("mid") or [""])[0]
    idx = (q.get("idx") or [""])[0]
    sn = (q.get("sn") or [""])[0]
    if mid:
        return f"{mid}_{idx or '1'}_{sn[:8]}"
    return url[-40:]


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        # FTS5 是否真的可用，直接在这里验一次。
        # 不支持的话应该立刻报错退出，而不是等用户搜的时候才发现搜不出东西。
        try:
            self.conn.execute("SELECT count(*) FROM articles_fts LIMIT 1")
        except sqlite3.OperationalError as exc:  # pragma: no cover
            raise UpstreamError(
                f"SQLite 缺少 FTS5 支持: {exc}",
                hint="本工具的中文全文检索依赖 FTS5；请使用带 FTS5 的 Python（CPython 官方构建已包含）",
            ) from exc
        self.conn.commit()

    # ---------- 账号 ----------

    def upsert_account(
        self,
        username: str,
        nick: str = "",
        user_name: str = "",
        media_name: str = "",
        enabled: bool = True,
        added_at: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO accounts(username, user_name, nick, media_name, enabled, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
              user_name  = COALESCE(NULLIF(excluded.user_name, ''),  accounts.user_name),
              nick       = COALESCE(NULLIF(excluded.nick, ''),       accounts.nick),
              media_name = COALESCE(NULLIF(excluded.media_name, ''), accounts.media_name),
              enabled    = excluded.enabled
            """,
            (username, user_name, nick, media_name, 1 if enabled else 0, added_at),
        )
        self.conn.commit()

    def set_account_error(self, username: str, message: str | None) -> None:
        self.conn.execute(
            "UPDATE accounts SET last_error = ? WHERE username = ?",
            (message, username),
        )
        self.conn.commit()

    def mark_run(self, username: str, when: int) -> None:
        """记录运行时刻。

        ⚠️ accounts.last_run_at 是 TEXT 列（要存 ISO 时间给前端看），
        但这里写入的是 Unix 秒。读取方必须按整数解析 —— 见 stats() 里的 CAST。
        """
        self.conn.execute(
            "UPDATE accounts SET last_run_at = ? WHERE username = ?",
            (str(when), username),
        )
        self.conn.commit()

    def remove_account(self, username: str, keep_data: bool = True) -> None:
        self.conn.execute("DELETE FROM accounts WHERE username = ?", (username,))
        if not keep_data:
            # 触发器会同步清理 FTS 索引
            self.conn.execute("DELETE FROM articles WHERE account = ?", (username,))
        self.conn.commit()

    def list_accounts(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM accounts ORDER BY added_at, nick"))

    # ---------- 文章 ----------

    def known_urls(self, account: str | None = None) -> set[str]:
        """已收录的 URL 集合，供去重比对。

        只取 URL 一列 —— 日常轮询时这是最省的查询。
        """
        if account:
            rows = self.conn.execute("SELECT url FROM articles WHERE account = ?", (account,))
        else:
            rows = self.conn.execute("SELECT url FROM articles")
        return {r["url"] for r in rows}

    def has_article(self, url: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM articles WHERE url = ? LIMIT 1", (url,)).fetchone()
        return row is not None

    def insert_article(self, art: ArticleRow) -> bool:
        """插入一篇文章。已存在则返回 False，不覆盖。

        返回 False 是正常路径（重复轮询），不是错误。
        """
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO articles
              (url, account, title, digest, content, published, collected, raw_path, exported)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                art.url,
                art.account,
                art.title,
                art.digest,
                art.content,
                art.published,
                art.collected,
                art.raw_path,
            ),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def mark_exported(self, urls: list[str]) -> None:
        if not urls:
            return
        self.conn.executemany("UPDATE articles SET exported = 1 WHERE url = ?", [(u,) for u in urls])
        self.conn.commit()

    def recent(self, account: str | None = None, limit: int = 20) -> list[sqlite3.Row]:
        """最近文章。

        返回**全部列**（含 content） —— 因为导出路径也用这个方法（`export --all`），
        只 select 几列会让 render_markdown 拿不到正文。
        内容列在这里多花一点内存，换取"所有取行方法返回同构 row"这个简单契约。
        """
        sql = "SELECT * FROM articles"
        args: list = []
        if account:
            sql += " WHERE account = ?"
            args.append(account)
        sql += " ORDER BY published DESC LIMIT ?"
        args.append(limit)
        return list(self.conn.execute(sql, args))

    def get_article(self, url: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM articles WHERE url = ?", (url,)).fetchone()

    def unexported(self, since: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM articles WHERE exported = 0"
        args: list = []
        if since is not None:
            sql += " AND published >= ?"
            args.append(since)
        sql += " ORDER BY published"
        return list(self.conn.execute(sql, args))

    # ---------- 检索 ----------

    def search(
        self,
        keyword: str,
        account: str | None = None,
        since: int | None = None,
        limit: int = 20,
    ) -> list[sqlite3.Row]:
        """全文检索。

        ⚠️ 这里有个实测踩到的硬限制：**trigram 分词器只索引 3 字符及以上的片段**。
        所以搜「南向资金」（4 字）能正常命中，搜「港股」（2 字）用 FTS5 会返回 0 条 ——
        这不是没数据，是分词器根本不索引这么短的片段。

        对策：按关键词长度分流。
          - 长度 >= 3：走 FTS5，能拿到 snippet 高亮，速度快
          - 长度 < 3 ：退回 LIKE 扫描，结果多一列手写的摘录

        代价是 2 字词会全表扫描。本工具的单库规模在几千到几万篇之间，
        LIKE 扫描仍在毫秒级，可以接受。
        """
        term = keyword.replace('"', " ").strip()
        if not term:
            return []

        # 双字以下 FTS5 的 trigram 索引不覆盖，必须走 LIKE
        use_fts = len(term) >= 3

        if use_fts:
            sql = """
                SELECT a.url, a.account, a.title, a.published, a.collected,
                       snippet(articles_fts, 1, '<<', '>>', '…', 16) AS snip
                FROM articles_fts
                JOIN articles a ON a.rowid = articles_fts.rowid
                WHERE articles_fts MATCH ?
            """
            args: list = [f'"{term}"']
        else:
            sql = """
                SELECT a.url, a.account, a.title, a.published, a.collected,
                       substr(a.content, max(1, instr(a.content, ?) - 12), 48) AS snip
                FROM articles a
                WHERE (a.content LIKE ? OR a.title LIKE ?)
            """
            args = [term, f"%{term}%", f"%{term}%"]

        if account:
            sql += " AND a.account = ?"
            args.append(account)
        if since is not None:
            sql += " AND a.published >= ?"
            args.append(since)
        sql += " ORDER BY a.published DESC LIMIT ?"
        args.append(limit)
        return list(self.conn.execute(sql, args))

    # ---------- 统计 ----------

    def stats(self) -> dict:
        one = lambda sql, *a: self.conn.execute(sql, a).fetchone()[0]  # noqa: E731
        size = self.path.stat().st_size if self.path.exists() else 0
        # last_run_at 是 TEXT 列，用 CAST 取回整数
        raw_last = one("SELECT max(CAST(last_run_at AS INTEGER)) FROM accounts")
        return {
            "accounts": one("SELECT count(*) FROM accounts"),
            "accounts_enabled": one("SELECT count(*) FROM accounts WHERE enabled = 1"),
            "articles": one("SELECT count(*) FROM articles"),
            "exported": one("SELECT count(*) FROM articles WHERE exported = 1"),
            "db_bytes": size,
            "fts_available": True,
            "last_run": int(raw_last) if raw_last is not None else None,
            "errors": [
                dict(r)
                for r in self.conn.execute(
                    "SELECT username, nick, last_error FROM accounts WHERE last_error IS NOT NULL"
                )
            ],
        }

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def write_raw_atomic(base_dir: Path, account: str, published: int, ident: str, payload: dict) -> Path:
    """原子写入原始响应。

    这份存档是换上游时的救命底牌：只要它在，将来可以无损重建全部衍生数据，不用重抓。
    """
    target_dir = base_dir / account
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{published}_{ident}.json"
    fd, tmp_name = tempfile.mkstemp(dir=str(target_dir), prefix=".raw-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def now_ts() -> int:
    return int(time.time())
