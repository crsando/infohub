# xnews 设计方案

> 状态：实现契约；当前代码已覆盖 M1–M5 的本地功能，现场 API 验证仍需真实 token
>
> 目标：在 `infohub/xnews` 下建立独立的 Python/uv 子项目，通过配置文件维护一组 X/Twitter 账号，轮询 TiKHub 获取新帖子，跨账号和跨运行去重，并同时保存原始 JSON 与解析后的 Markdown。
>
> 参考实现：[wxmp](../../wxmp/)，但不复制公众号特有的 URL 和正文详情规则。

## 1. 目标与边界

### 1.1 目标

- 配置文件声明监听账号、轮询参数和本地输出位置。
- 通过 TiKHub X/Twitter Web 接口读取用户时间线。
- 每轮获取最新帖子，以稳定帖子 ID 幂等去重。
- 同一帖子被多个监听账号发现时只保存一份正文，同时保存来源关系。
- 保存 TiKHub 原始 JSON，可离线重新解析和重建 Markdown。
- 为每条帖子生成可检索、可重复导出的 Markdown。
- 单账号失败不阻断其他账号，并记录运行状态。
- 使用 `uv` 管理环境和执行，`pytest` 锁住配置、解析、去重、导出行为。

### 1.2 非目标

- 不执行发帖、回复、点赞、转发、关注或取消关注。
- 不把轮询接口当成实时 Streaming；实时性由间隔决定。
- 第一版不下载媒体文件，只保存媒体实体和 URL。
- 不承诺完整历史；每轮从第一页开始，可配置本轮页数。
- 不把显示名称、文本或时间当成账号/帖子稳定主键。

## 2. 已确定的 TiKHub 接口

默认 Host：`https://api.tikhub.io`。认证：

```http
Authorization: Bearer <TIKHUB_TOKEN>
```

| 能力 | 方法与路径 | 参数 | 用途 |
|---|---|---|---|
| 搜索账号/帖子 | `GET /api/v1/twitter/web/fetch_search_timeline` | `keyword`、`search_type=People`、`cursor` | `add` 时解析账号 |
| 用户资料 | `GET /api/v1/twitter/web/fetch_user_profile` | `screen_name` 或 `rest_id` | 核验账号、补全 ID |
| 用户帖子 | `GET /api/v1/twitter/web/fetch_user_post_tweet` | `screen_name` 或 `rest_id`、`cursor` | 主采集接口 |
| 关注列表 | `GET /api/v1/twitter/web/fetch_user_followings` | `screen_name`、`cursor` | 可选关系数据 |

帖子响应重点是 `data.timeline`、`data.pinned`、`data.next_cursor`。`pinned` 与时间线分开，默认不计入“最新帖子”。关注列表没有关注时间，不能据此判断“最近关注的人”。

Provider 层必须保存完整 envelope，并把字段差异限制在 parser 适配器中；CLI、数据库和导出不能直接依赖单一嵌套结构。

## 3. 技术选型与目录

| 维度 | 选择 | 理由 |
|---|---|---|
| 语言 | Python 3.12+ | 与 `wxmp` 一致，标准库足够处理配置、SQLite、CLI |
| 执行 | `uv` | 锁定依赖和 Python 环境 |
| HTTP | `requests==2.32.3` | 与 `wxmp` 一致，Session/重试直接可用 |
| 存储 | SQLite + FTS5 | 单文件、事务、全文检索、易备份 |
| CLI | `argparse` | 少依赖并保持 wxmp 习惯 |
| 测试 | `pytest` | 仅开发期依赖 |

实现后的布局：

```text
xnews/
├── README.md
├── pyproject.toml
├── uv.lock
├── .gitignore
├── xnews                         # 源码树直跑入口
├── src/xnews/
│   ├── __init__.py
│   ├── cli.py                    # CLI、输出、退出码
│   ├── config.py                 # 配置读取、校验、原子写回
│   ├── paths.py                  # XNEWS_CONFIG/XNEWS_DATA_DIR
│   ├── api.py                    # TiKHub Session、鉴权、限速、重试
│   ├── resolve.py                # 名称/用户名/rest_id → 资料
│   ├── parser.py                 # 响应适配、帖子标准化
│   ├── pipeline.py               # 单轮采集、去重、raw、入库
│   ├── store.py                  # SQLite、事务、FTS5
│   ├── export.py                 # Markdown 渲染、原子写
│   └── errors.py
├── tests/fixtures/               # 脱敏真实响应样本
├── tests/                        # 配置/API/parser/store/pipeline/export
├── examples/
└── docs/DESIGN.md
```

运行时数据默认在 `~/.local/share/xnews`，配置默认在 `~/.config/xnews/config.json`，不放进仓库。

## 4. 配置设计

### 4.1 路径和 token

配置路径优先级：

1. `XNEWS_CONFIG`
2. `$XDG_CONFIG_HOME/xnews/config.json`
3. `~/.config/xnews/config.json`

数据目录优先级：

1. `XNEWS_DATA_DIR`
2. `storage.data_dir`
3. `~/.local/share/xnews`

token 优先级固定为：

```text
TIKHUB_TOKEN（非空白） > provider.token > 缺少凭据
```

环境变量不写回配置。`config.json` 加入 `.gitignore`，权限为 `0600`；日志、异常、raw 元数据和测试快照不得含 token 或 Authorization 头。配置修改使用临时文件、`fsync`、`os.replace`。

### 4.2 配置示例

```json
{
  "version": 1,
  "provider": {
    "name": "tikhub",
    "base_url": "https://api.tikhub.io",
    "token": "",
    "timeout": 60,
    "retry": { "max": 3, "backoff": [2, 5, 15] },
    "rate_limit": { "qps": 1 }
  },
  "schedule": {
    "timezone": "Asia/Shanghai",
    "interval_seconds": 900,
    "jitter_seconds": 0
  },
  "storage": {
    "data_dir": null,
    "keep_raw": true,
    "keep_response_snapshots": true
  },
  "export": {
    "enabled": true,
    "dir": "",
    "layout": "by_account",
    "filename": "{date} {screen_name} {id}.md",
    "include_media_links": true,
    "include_pinned": false
  },
  "collect": {
    "pages": 1,
    "max_posts_per_account": 50,
    "stop_at_known": true,
    "known_streak": 1
  },
  "accounts": [
    {
      "key": "macro-margin",
      "nick": "MacroMargin",
      "screen_name": "MacroMargin",
      "rest_id": "",
      "enabled": true,
      "pages": null,
      "max_posts": null,
      "include_pinned": null,
      "added_at": "2026-10-03T12:00:00+08:00",
      "last_run_at": null,
      "last_error": null
    }
  ]
}
```

校验规则：

- `version` 当前为 `1`；不兼容变化通过迁移处理。
- base URL 必须为 HTTP(S)；超时为正整数；重试非负；QPS 小于等于 0 表示不限速。
- `interval_seconds` 只用于 `watch`；`run` 只跑一轮。
- 全局 `pages`、`max_posts_per_account` 大于 0；账号覆盖字段为 `null` 时继承全局值。
- `key` 不重复；至少有 `screen_name` 或 `rest_id`；`screen_name` 不含 `@`。
- `layout` 只能是支持的布局，空账号和重复账号直接报配置错误。

`init` 创建的配置/数据目录为 `0700`，配置、SQLite、raw 为 `0600`，Markdown 默认 `0600`。`xnews check --fix-permissions` 只修复 xnews 管理的路径，不递归修改用户指定导出目录中的其他文件。

## 5. 账号解析和采集

### 5.1 添加和稳定身份

`xnews add` 支持：

1. `@username` 或 `username`：调用 profile 核验。
2. 数字 `rest_id`：用 ID 调 profile。
3. 显示名称/模糊关键词：调用 `fetch_search_timeline`，固定 `search_type=People`，展示候选后选择。

候选至少显示 `name`、`screen_name`、`rest_id`、简介、头像 URL。多候选的非交互运行直接退出，`--pick N` 用于脚本明确选择。搜索 cursor 只活在本次解析流程，不写入配置。

添加前按 `rest_id`、规范化 `screen_name`、本地 `key` 三层查重。昵称只用于展示。账号改名时保留 `rest_id` 和本地 key，只更新 screen_name。

### 5.2 单轮数据流

```text
读取并校验 config.json
    │
    ▼
筛选 enabled 账号
    │
    ├─ fetch_user_post_tweet(screen_name/rest_id, cursor)
    │       ├─ 原样保存 raw/responses/<run_id>/<account>/page-001.json
    │       ├─ parser 提取 timeline / pinned / next_cursor
    │       ├─ 本轮按 post_id 去重
    │       ├─ 已知帖子：只更新 post_accounts 关系
    │       └─ 新帖/内容更新：保存 post raw、入库、生成 Markdown
    │
    ├─ 提交账号事务；cursor 只在本轮继续翻页
    └─ 更新账号状态并写入 runs 汇总
```

默认一账号一页。`pages > 1` 时使用 `data.next_cursor`，但 cursor **不跨运行保存**；下一轮始终从第一页开始。时间线假定新到旧排序，达到页数/帖子数、没有 next_cursor，或遇到配置的已知帖子连续数后停止。`stop_at_known` 是成本优化，关闭后可补历史。

`data.pinned` 默认只存 raw，不计入最新数量、不导出；`include_pinned=true` 时单独导出并在 frontmatter 标记。它若出现在 timeline，仍按 post_id 去重。

### 5.3 标准化对象

Provider 只做 HTTP，`parser.py` 输出：

```python
NormalizedPost(
    post_id: str,             # rest_id/tweet_id/id_str/id
    author_rest_id: str,
    author_screen_name: str,
    author_name: str,
    text: str,
    created_at: int | None,   # UTC Unix 秒
    url: str,
    is_pinned: bool,
    is_reply: bool,
    is_repost: bool,
    entities: dict,
    raw_object: dict,
    content_hash: str,
)
```

ID 按 `rest_id`、`tweet_id`、`id_str`、`id` 的优先级提取；文本按 `note_tweet.text`、`full_text`、`text` 提取；作者优先从 `user_info`/`author` 获取；时间统一 UTC；没有 URL 时生成 `https://x.com/<screen_name>/status/<post_id>`。媒体、链接、标签和回复关系放入 `entities`，未知字段仍由 raw 保留。

解析器是纯函数，给定同一 JSON 得到同一对象。无稳定 ID 的对象不伪造正式帖子，只保留 page raw 并记录解析告警。相同 ID 的文本/实体变化更新当前快照和 hash，旧 raw snapshot 保留；删除/冻结占位不覆盖已有正文。

## 6. 去重、SQLite 和事务

### 6.1 去重规则

唯一主键是帖子稳定 ID：

- `posts.post_id` 跨运行唯一；重复发现只更新关系或内容快照。
- 同一帖被多个监听项发现时正文一行，通过 `post_accounts` 记录来源。
- timeline、pinned、嵌套重复对象先在内存按 ID 去重。
- URL 只展示和辅助索引，不作为主键。
- 无 ID 对象不进入正式帖子表，避免错误合并。

### 6.2 核心 schema

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE watch_accounts (
  key TEXT PRIMARY KEY,
  nick TEXT NOT NULL,
  screen_name TEXT NOT NULL,
  rest_id TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  added_at TEXT NOT NULL,
  last_run_at INTEGER,
  last_error TEXT
);
CREATE UNIQUE INDEX idx_watch_rest_id ON watch_accounts(rest_id)
  WHERE rest_id IS NOT NULL AND rest_id <> '';

CREATE TABLE posts (
  post_id TEXT PRIMARY KEY,
  author_rest_id TEXT,
  author_screen_name TEXT,
  author_name TEXT,
  text TEXT NOT NULL,
  created_at INTEGER,
  collected_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  url TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  is_pinned INTEGER NOT NULL DEFAULT 0,
  is_reply INTEGER NOT NULL DEFAULT 0,
  is_repost INTEGER NOT NULL DEFAULT 0,
  entities_json TEXT NOT NULL DEFAULT '{}',
  raw_path TEXT,
  markdown_path TEXT,
  markdown_hash TEXT,
  parser_version TEXT NOT NULL
);

CREATE TABLE post_accounts (
  post_id TEXT NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
  watch_key TEXT NOT NULL REFERENCES watch_accounts(key) ON DELETE CASCADE,
  first_seen_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  PRIMARY KEY (post_id, watch_key)
);

CREATE TABLE raw_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id TEXT REFERENCES posts(post_id) ON DELETE SET NULL,
  watch_key TEXT,
  run_id INTEGER,
  path TEXT NOT NULL UNIQUE,
  collected_at INTEGER NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at INTEGER NOT NULL,
  ended_at INTEGER,
  status TEXT NOT NULL,
  message TEXT
);

CREATE TABLE run_accounts (
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  watch_key TEXT NOT NULL,
  status TEXT NOT NULL,
  pages INTEGER NOT NULL DEFAULT 0,
  fetched INTEGER NOT NULL DEFAULT 0,
  new_count INTEGER NOT NULL DEFAULT 0,
  updated_count INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  PRIMARY KEY (run_id, watch_key)
);

CREATE VIRTUAL TABLE posts_fts USING fts5(
  author_screen_name, author_name, text,
  content='posts', content_rowid='rowid', tokenize='trigram'
);
```

`posts_fts` 用触发器与 `posts` 同步，中文短词沿用 `wxmp` 的 LIKE 兜底。`posts` 是当前解析快照，`raw_snapshots` 是历史原始响应索引，因此可以离线重导 Markdown。

### 6.3 事务边界

每个账号每页：

1. HTTP 返回后用临时文件 + `fsync` 写 response raw。
2. 解析并为新/变化帖子写 post raw snapshot。
3. 一个 SQLite 事务 upsert posts、插入 post_accounts、更新 run_accounts。
4. 事务提交后写 Markdown；导出失败不回滚采集。
5. Markdown 成功后更新 markdown path/hash。

中断最多留下孤立 raw，不会有截断 JSON 或半个 SQLite 事务；`stats` 报告孤立文件。

## 7. 文件和 Markdown

默认数据目录：

```text
~/.local/share/xnews/
├── xnews.db
├── raw/
│   ├── responses/<run_id>/<watch_key>/page-001.json
│   └── posts/<post_id>/<collected_at>.json
└── logs/                         # 默认不落敏感日志
```

导出目录由 `export.dir` 指定，可按作者建子目录。文件名使用清洗后的日期、作者、帖子 ID，并追加稳定 ID/hash，不直接使用帖子正文。

Markdown 形态：

```markdown
---
平台: X
作者: MacroMargin (@MacroMargin)
作者ID: "123456789"
帖子ID: "987654321"
发布时间: 2026-10-03 10:30:00+00:00
采集时间: 2026-10-03 10:31:12+08:00
置顶: false
回复: false
转发: false
原文: https://x.com/MacroMargin/status/987654321
监听账号: [MacroMargin]
---

帖子正文……

## 链接与媒体

- https://example.com/source
```

不写重复 H1；frontmatter 值安全转义，正文不进入 frontmatter。新帖自动导出；内容 hash 变化时重写；hash 未变化时不改 mtime。`export --all` 从当前数据库重建，`reparse` 从 raw 重建，两者均不请求上游。所有文件原子写入。

## 8. API 客户端、错误和安全

`api.py` 负责带浏览器风格 User-Agent 的 Session、Bearer 认证、QPS 限速和有限重试。超时、429、5xx 可按 backoff 重试；401/403、404/422 等确定性错误立即返回。先检查 HTTP 状态，再检查业务 `code`、`message`/`msg`。异常和 CLI 输出必须脱敏，raw 只写 JSON body 与非敏感请求元数据，不写 Authorization。

退出码沿用 wxmp：

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 一般错误 |
| 2 | 参数错误 |
| 3 | 配置/权限错误 |
| 4 | token/鉴权错误 |
| 5 | 上游/限流错误 |
| 6 | 多候选需人工选择 |

账号错误写入 `run_accounts.error`，继续下一个账号；所有账号失败时 run 返回上游错误。

## 9. CLI

| 命令 | 行为 |
|---|---|
| `xnews init` | 创建配置、数据目录和权限 |
| `xnews add <name\|@handle\|rest_id>` | 搜索/核验账号，可选首次抓取 |
| `xnews remove <key> [--purge]` | 删除监听关系；默认保留帖子，purge 只删除无其他来源的帖子 |
| `xnews list [--json]` | 列出监听项和最近状态 |
| `xnews enable/disable <key>` | 启停监听 |
| `xnews run` | 一轮采集，支持 account/pages/max-posts/dry-run |
| `xnews watch` | 按 interval 持续轮询，支持 once、account、SIGINT/SIGTERM |
| `xnews search <keyword>` | 本地全文检索，支持 account/since/limit/json |
| `xnews show` | 列出最近帖子和 raw/Markdown 状态 |
| `xnews export [--all]` | 本地导出，不请求上游 |
| `xnews reparse` | 从 raw 重新解析和导出 |
| `xnews stats` | 账号、帖子、raw、导出、失败和 FTS 状态 |
| `xnews check [--live] [--fix-permissions]` | 检查配置/权限/数据库；只有 live 才请求上游 |

`watch` 每轮结束后等待间隔；错误使用上限退避；收到信号后完成当前原子操作再退出。生产上仍推荐 systemd/cron 调用 `run`，长进程不是唯一可靠性方案。

## 10. 测试方案

默认测试不访问 TiKHub；live 测试必须显式 `XNEWS_LIVE=1`，不打印 token。

- 配置：token 优先级、XNEWS_CONFIG/XNEWS_DATA_DIR、schema 校验、原子写、权限、账号三层查重。
- API：路径/方法/query/Bearer/UA，401/403/429/5xx/业务 code，分页 cursor 只在本轮传递，异常无 token。
- Parser：timeline/pinned、user_info、多种 ID/时间/媒体字段、重复对象、无 ID 告警、编辑 hash、删除占位。
- Store：schema/FTS5、跨运行和跨账号只一行 posts、post_accounts 关系、短词 LIKE 兜底、事务回滚。
- Pipeline：已知帖不重复处理，单账号失败隔离，页数/停止原因，raw 写入和运行汇总。
- Export：安全文件名、frontmatter、媒体/链接、内容更新重写、hash 未变不改 mtime、原子 Markdown。
- Offline：export --all、reparse 没有 token 仍可完成。

## 11. 实施阶段

### M0：契约和样本

建立目录与 fixture，确认真实 timeline/pinned/cursor 字段，冻结配置、`NormalizedPost` 和 schema 版本。

### M1：骨架和配置

建立 pyproject/lock/入口/ignore，实现路径、权限、配置校验、token 优先和离线 init/list/add/remove/enable/disable。

### M2：Provider 和 parser

实现 Session、UA、Bearer、限速、重试、四个 endpoint 薄方法和 parser fixture 测试。

### M3：SQLite、raw、幂等流水线

实现 schema、触发器、post ID 去重、post_accounts、按页/按帖 raw、事务、run dry-run 和失败隔离。

### M4：Markdown 和查询

实现安全 frontmatter、文件名清洗、原子导出、search/show/export/stats/check/reparse。

### M5：watch 和现场验证

实现轮询、退避、信号处理；用环境变量 token 做一次小规模 live 验证，默认单账号、第一页、限制帖子数。

每阶段结束运行 `uv run pytest -q`；live 验证不作为默认 CI 门槛。

## 12. 风险、取舍和验收

| 风险 | 应对 |
|---|---|
| 上游字段变化 | Provider/parser 分层，保存 envelope 和 fixture，支持 reparse |
| cursor 绑定快照 | 只在单次 run 内使用，每轮从第一页开始 |
| 费用/限流 | 默认一页、QPS、已知帖停止、账号串行 |
| 用户改名 | 以 rest_id 为长期身份，key 不变 |
| 跨账号重复 | posts.post_id 全局唯一，post_accounts 记录来源 |
| 编辑/删除/置顶 | content hash、raw 版本、pinned 独立标记 |
| token 泄露 | 环境变量优先、0600、日志脱敏、raw 不含请求头 |
| 导出失败 | 先提交 raw/数据库，导出可重试 |
| SQLite 无 FTS5 | 初始化主动报错，不返回假空结果 |

验收必须覆盖：

1. `TIKHUB_TOKEN` 优先且不出现在输出/文件。
2. 两个监听账号运行两轮，同一帖子仅一行 posts、一份 Markdown，有两条来源关系。
3. 同 ID 内容变化保留新 raw 并更新 Markdown。
4. 一个账号 429/5xx 时其他账号完成，run 记录错误。
5. 删除监听项不误删仍有来源的帖子；`--purge` 只删无关系帖子及派生文件。
6. 无 token/断网时 `export --all` 和 `reparse` 仍工作。
7. 中断或写入失败不产生截断 JSON、Markdown 或半个事务。
