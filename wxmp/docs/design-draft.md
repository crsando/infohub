> **历史文档**：这是开发前的设计稿（v0.2），保留作决策记录。
> 实际实现与之有差异，以 [DESIGN.md](DESIGN.md) 为准；差异与开发中发现的坑见 DESIGN.md §4。

# wxmp — 微信公众号订阅采集工具 · 开发方案设计稿

> 版本：v0.2（设计稿，待确认）
> 日期：2026-10-02
> 状态：**未开发**，等待确认
>
> v0.2 变更：主语言定案 Python（理由重写）；HTTP 库定案 `requests`；
> token 改为直接放 config.json（去掉环境变量间接层）；
> 定时调度整节标记为【延后实施】

---

## 0. 一句话定位

`wxmp` 是一个**单命令入口**的公众号订阅采集器：你在 `config.json` 里声明要订阅哪些号、每天几点拉，
它到点把新文章的**全文**抓下来存进本地 SQLite，并导出成 Markdown 给 Obsidian / 局域网共享。

**不做**：互动数据（阅读/点赞/在看/评论）——已确认不需要，且上游接口也拿不到。

---

## 1. 技术选型与理由

| 维度 | 选择 | 理由 |
|---|---|---|
| 主语言 | **Python 3.12**（`uv` 管理） | 见下方专节论证 |
| 存储 | **SQLite + FTS5** | 单文件、可拷走、可跨平台；局域网共享只需共享一个 `.db` |
| HTTP | **`requests`** | 见下方专节论证 |
| CLI | `argparse`（标准库） | 不引入 typer/click，保持依赖最少 |
| 入口 | 单一可执行脚本 `wxmp` | 见 §5 |
| 定时 | **不内建守护进程** | 【延后实施】见 §7 |

**最终依赖清单只有 1 个外部包**：`requests`。
`sqlite3`、`argparse`、`json`、`datetime` 全部是标准库 —— 这是选 Python 的核心收益。

### 1.1 为什么主语言是 Python

**先排除伪理由：性能与这个项目无关。**

真实负载是一天跑 2 次、每次 1–2 个 HTTP 请求，单次瓶颈是**上游响应时间 8–16 秒**。
一年攒几百篇文章，数据库总共几 MB。Go 比 Python 快 50 倍，在这个项目里等于把 8.1 秒变成 8.1 秒。
**任何以"性能"推荐语言的理由，在这个项目里都是伪命题。**

**真正的风险排序**（这决定了选型）：

1. **上游接口会变** —— 已实证：WeWe RSS 被归档、跨公众号列表接口 2026-07 集体报 `200013`。
   **"换上游"是 when 不是 if。**
2. **中文全文检索** —— FTS5 必须用 trigram 分词，踩错了搜"宁德时代"搜不出来。
3. **配置文件表达力** —— 订阅列表 + 号级时刻覆盖 + 各种开关，会持续演化。
4. **可读性** —— 半年后可能凌晨排查"今天为什么没拉到"，得你自己看得懂。

**逐条对应到 Python 的优势**：

| 风险 | Python 的应对 |
|---|---|
| 上游字段漂移 | **动态类型**：字段名变了改一行；配 `.get()` 天然容忍字段缺失，不会像 Go/Rust 那样编译期拦死 |
| 中文检索 | `sqlite3` **标准库内置**，实测 3.46.1 且 **FTS5 可用**，`tokenize='trigram'` 直接能写 |
| 配置校验 | `pydantic`（可选）给出人话报错，而不是崩栈 |
| 无人值守稳定性 | 依赖只有 1 个包，**锁版本即可**，升级踩雷面极小 |

**为什么不是 Node**：

- Node 已内建 `node:sqlite`，但 **FTS5 支持要自己编译 SQLite**，平白多一道坎。
- WSL 里 **Node 装了但没挂 PATH**（nvm 内有 v24.20.0），每次执行要 `source ~/.nvm/nvm.sh`。
- Node 的合理位置是 **v0.2 的局域网只读查询站** —— 那跟核心采集器是**两个独立进程、
  两个独立关注点**，不该混在一起。核心越简单越好，它是要长期无人值守跑的东西。

**为什么不是 Go / Rust**：

- Go 的优势是"单二进制拷走就能跑"，但这个项目**就跑在你这台机器上**，不需要拷走。
  换来的是上游字段一变就得重新编译 + JSON 解析样板代码。不划算。
- Rust：抓个文章而已，上重炮了。

**Python 的唯一真实缺点**：依赖打包。半年后 Python 从 3.12 跳到 3.14，某个包可能不兼容。
**对策**：只依赖 1 个 HTTP 包并锁死版本 —— 这正是 §1 依赖策略的由来。

### 1.2 为什么 HTTP 用 `requests` 而不是 `httpx`

**结论：`requests`。** 三条理由：

**① 它基本不坏了。** `requests` 2.x 已冻结多年，API 十年没动。`httpx` 仍在 1.0 之前，
近期小版本对代理参数、transport 做过**破坏性改动**。本项目的头号风险是"半年后上游变了要改代码"，
不应该再叠一个"HTTP 库自己变了"的风险源。

**② 它更轻。** `requests` → urllib3 + certifi + charset-normalizer + idna。
`httpx` 额外拖进 **anyio + sniffio**（异步运行时）。一个纯同步、一天跑两次的采集器，
为异步库付依赖成本是白给。

**③ 并发需求用线程就够，不需要协程。** 将来订到几十个号要并发拉，用
`concurrent.futures.ThreadPoolExecutor` 即可 —— 8 秒的阻塞 I/O 正是线程的适用场景，
而且对同步代码来说**线程比协程简单得多**，半年后你自己看代码时这点很重要。

**`httpx` 唯一更强的地方**：超时粒度更细（connect/read/write/pool 分开设）。
本项目确实要等 8–16 秒响应，有点用，但 `requests` 的 `timeout=(连接, 读取)` 元组够使，
不值得为此换库。

**什么情况该改口选 `httpx`**：订阅量上到 20+ 个号、要真正高并发、且愿意用 asyncio 重写。
按当前只有「仓都加满」一个号，这个前提不成立。

> **实施要求**：`pyproject.toml` 中 `requests` **必须锁到具体版本**（如 `requests==2.32.x`），
> 不开区间。这是"依赖只有 1 个"这个优势能成立的前提。

---

## 2. 平台假设（按你的要求：Linux / macOS 语义）

代码**只写 POSIX 语义**，不出现任何 Windows 路径、盘符、`\\` 分隔符、cmd 语法。

### 2.1 路径解析（XDG Base Directory）

| 用途 | 解析规则 |
|---|---|
| 配置目录 | `$WXMP_CONFIG_DIR` → `$XDG_CONFIG_HOME/wxmp` → `~/.config/wxmp` |
| 数据目录 | `$WXMP_DATA_DIR` → `$XDG_DATA_HOME/wxmp` → `~/.local/share/wxmp` |
| 缓存/日志 | `$XDG_CACHE_HOME/wxmp` → `~/.cache/wxmp` |

### 2.2 config.json 定位（你的要求 2）

**通过环境变量 `WXMP_CONFIG` 指定绝对路径**，未设置时回落到默认位置：

```sh
export WXMP_CONFIG=/srv/wxmp/config.json   # 显式指定
./wxmp run                                  # 读这个文件
# 未设置时 → ~/.config/wxmp/config.json
```

所有子命令都遵循这一条，**没有任何命令支持 `--config` 之外的临时路径覆盖**，
保证"一个环境变量决定一切"。

### 2.3 但本项目位于 Windows 盘，WSL 访问方式

本项目目录 `.../wechat-automation-monitoring-research/outputs/` 在 **Windows 盘**上。
从 WSL 里访问它是 `/mnt/c/Users/qiumi/Documents/Alma/2026-10-02/wechat-automation-monitoring-research/outputs/`。

**开发期**：在 outputs 里写代码，通过 `/mnt/c/...` 路径在 WSL 中执行。
**运行期**：建议把 `wxmp` 装到 WSL 的 `~/.local/bin/wxmp`，数据放 `~/.local/share/wxmp`（WSL 内原生路径，性能好得多）。

> ⚠️ **重要且必须由你拍板的取舍**：`/mnt/c` 走的是 9p 协议，I/O 比 WSL 原生文件系统慢很多。
> SQLite 放在 `/mnt/c` 上频繁写入会明显卡顿。
> **我的建议**：代码在 outputs（便于你在 Windows 侧看/改），**数据目录默认落 WSL 原生路径** `~/.local/share/wxmp`，
> 但 `storage.data_dir` 可在 config.json 里覆盖成 `/mnt/c/...` —— 你要挂到 Obsidian 的话，Markdown 导出本来就得走 `/mnt/c`。

---

## 3. 目录布局

### 3.1 代码仓库（`outputs/wxmp/`）

```
outputs/wxmp/
├── wxmp                     # 单一可执行入口（Python，带 shebang）
├── pyproject.toml           # uv 依赖声明
├── README.md                # 完整使用文档（你的要求 4）
├── docs/
│   ├── DESIGN.md            # 本设计稿的定稿版
│   ├── CONFIG.md            # config.json 全字段参考
│   ├── CLI.md               # 命令参考手册
│   └── TROUBLESHOOTING.md   # 常见错误与排查
├── examples/
│   ├── config.minimal.json  # 最小可用配置
│   └── config.full.json     # 全字段示例
├── src/wxmp/
│   ├── __init__.py
│   ├── cli.py               # 参数解析与子命令分发
│   ├── config.py            # 配置加载/校验/写回
│   ├── resolve.py           # 名称/URL → username 解析
│   ├── api.py               # TikHub HTTP 客户端（含 UA/重试/限速）
│   ├── store.py             # SQLite + FTS5 读写
│   ├── pipeline.py          # 采集主流程
│   ├── export.py            # Markdown 导出（Obsidian）
│   └── schedule.py          # 定时计划计算与 crontab/systemd 生成
└── tests/
    ├── test_resolve.py
    ├── test_store.py
    └── test_pipeline.py
```

### 3.2 运行时数据（默认 `~/.local/share/wxmp/`）

```
~/.local/share/wxmp/
├── wxmp.db              # SQLite 主库（含 FTS5 全文索引）
├── raw/
│   └── <username>/
│       └── <published>_<mid>_<idx>.json   # 原始响应，一字不改
└── logs/
    └── wxmp-YYYY-MM-DD.log
```

---

## 4. config.json 设计

### 4.1 完整结构

```jsonc
{
  "$schema": "https://raw.githubusercontent.com/.../wxmp/config.schema.json",
  "version": 1,

  // ── 定时：全局默认 + 每天多个时间点（你的要求 2）
  "schedule": {
    "timezone": "Asia/Shanghai",
    "default_times": ["09:00", "16:00"],   // 默认拉取时刻，HH:MM 24 小时制
    "catch_up_on_run": true                // 补跑：错过的时刻在下次启动时补齐
  },

  // ── 上游凭据（明文放这里，不做间接层）
  "provider": {
    "name": "tikhub",
    "base_url": "https://api.tikhub.io",
    "token": "<TIKHUB_API_KEY>",                 // TIKHUB_TOKEN 未设置时使用
    "timeout": 120,
    "retry": { "max": 3, "backoff": [2, 5, 15] },
    "rate_limit": { "qps": 1 }
  },

  // ── 存储
  "storage": {
    "data_dir": null,                       // null = 用默认 ~/.local/share/wxmp
    "keep_raw": true                        // 永久保留原始响应
  },

  // ── 导出（Markdown for Obsidian / 局域网共享）
  "export": {
    "enabled": true,
    "format": "markdown",
    "dir": "/mnt/c/Users/qiumi/obsidian-vault/Research/公众号摘录",
    "layout": "by_account",                 // by_account | flat
    "filename": "{date} {title}.md",
    "bold_keywords": true,                  // 关键股票名/代码加粗
    "lanshare": { "enabled": false }        // v0.2：局域网只读查询站
  },

  // ── 订阅列表
  "accounts": [
    {
      "nick": "仓都加满",                    // 显示名
      "username": "mtlsnow",                 // 拉取用标识（不可为空）
      "user_name": "gh_e2899e9a812e",        // 官方 gh_ 号（可选，留档）
      "media_name": "深圳和光同行传媒有限公司", // 主体（辨真伪用）
      "enabled": true,
      "times": null,                         // 号级覆盖；null = 用 default_times
      "first_page_only": true,               // 日常只拉第一页
      "added_at": "2026-10-02T11:20:00+08:00",
      "last_run_at": null,
      "last_seen_url": null
    }
  ]
}
```

### 4.2 字段设计要点

- **`default_times` 与 `times` 是覆盖关系**：号可以单独设定拉取时刻（比如发文不规律的号多拉几次）。
- **`username` 是唯一必需字段**。`nick` / `media_name` 只是给人看的。
- **凭据优先使用环境变量 `TIKHUB_TOKEN`，未设置时回退到 `provider.token`**。
  环境变量不会写回配置文件；配置字段仍是明文存放。唯一约束是下面这条：
  **`config.json` 不要提交进 git**（`.gitignore` 里排除）。
- **`catch_up_on_run`**：见 §7（延后实施），如果机器 9:00 没开机，下次启动时是否补拉。默认 `true`。

---

## 5. CLI 设计（你的要求 3）

**单一入口 `wxmp`**，所有功能挂在子命令下。

```sh
wxmp <command> [args]
```

### 5.1 命令总表

| 命令 | 用途 | 关键参数 |
|---|---|---|
| `wxmp init` | 初始化配置与数据目录 | `--force` 覆盖已有 |
| **订阅管理** | | |
| `wxmp add <目标>` | 新增订阅 | `--nick` `--time 09:00 --time 16:00` `--no-fetch` |
| `wxmp remove <key>` | 移除订阅 | `--keep-data` 保留已抓文章 |
| `wxmp list` | 列出订阅 | `--json` |
| `wxmp enable <key>` / `wxmp disable <key>` | 启停 | |
| **时刻管理**（写入 config 即可，**定时触发延后**） | | |
| `wxmp schedule` | 查看当前时刻表 | |
| `wxmp schedule set 09:00 16:00` | 设全局默认 | |
| `wxmp schedule set --account 仓都加满 09:00,12:00,16:00` | 设单号 | |
| `wxmp schedule unset --account 仓都加满` | 清除号级覆盖 | |
| ~~`wxmp schedule install`~~ | 【M6+ 延后】生成并安装 crontab/systemd | 见 §7 |
| **采集** | | |
| `wxmp run` | 手动刷新全部（一次） | `--account <key>` `--pages N` `--dry-run` |
| ~~`wxmp run --due`~~ | 【M6+ 延后】仅拉"到点该拉"的号 | 见 §7 |
| **查询与导出** | | |
| `wxmp search <关键词>` | FTS5 全文检索 | `--account` `--since` `--limit` `--json` |
| `wxmp show <key>` | 看某号最近文章 | `--limit 20` |
| `wxmp export` | 重新导出 Markdown | `--since 2026-01-01` `--account` |
| `wxmp stats` | 库统计 | 号数/文章数/占用/上次运行 |
| `wxmp check` | 连通性与配置体检 | 校验 token、账号有效、路径可写 |

### 5.2 `wxmp add` 的三种输入形态（关键）

```
wxmp add 仓都加满                              # ① 名称 → 走搜索接口解析
wxmp add https://mp.weixin.qq.com/s/xxxxx      # ② 文章 URL → 抽 __biz + 解析出账号
wxmp add mtlsnow --nick 仓都加满               # ③ 直接给标识（最快，不搜索）
wxmp add 仓都加满 --nick cangdu --time 08:30   # 带别名与专属时刻
```

**① 名称形态必须有消歧流程**（实测：搜"仓都加满"返回 14 条，含同名视频号、
蹭名的"仓都加满不要怂"）。行为设计：

- 命中 1 条 → 直接加入
- 命中多条 → **打印候选表（含主体公司名），要求交互确认**
- `--yes` 时取第一条精确匹配；`--pick N` 指定第 N 条
- 非交互环境（cron）遇到多候选 → **报错退出**，不猜

**主体公司名（`media_name`）是辨真伪的唯一可靠依据**，必须打印出来让人确认。

### 5.3 退出码约定

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 一般错误 |
| 2 | 参数错误 |
| 3 | 配置错误（文件缺失/JSON 非法/字段非法） |
| 4 | 凭据错误（token 缺失或无效） |
| 5 | 上游错误（网络/限流/接口异常，已重试） |
| 6 | 需要人工消歧（多候选且非交互） |

---

## 6. 存储设计

### 6.1 Schema

```sql
CREATE TABLE IF NOT EXISTS accounts (
  username    TEXT PRIMARY KEY,   -- mtlsnow
  user_name   TEXT,               -- gh_e2899e9a812e
  nick        TEXT,
  media_name  TEXT,
  enabled     INTEGER NOT NULL DEFAULT 1,
  added_at    TEXT,
  last_run_at TEXT,
  last_error  TEXT
);

CREATE TABLE IF NOT EXISTS articles (
  url         TEXT PRIMARY KEY,   -- ⭐ 去重主键
  account     TEXT NOT NULL REFERENCES accounts(username),
  title       TEXT,
  digest      TEXT,
  content     TEXT,               -- content_text 全文
  published   INTEGER NOT NULL,   -- create_time（微信侧发布）
  collected   INTEGER NOT NULL,   -- 本地抓取时刻
  raw_path    TEXT,               -- 指向 raw JSON
  exported    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_articles_pub ON articles(published DESC);
CREATE INDEX IF NOT EXISTS idx_articles_acc ON articles(account, published DESC);

-- 全文检索
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
  title, content,
  content='articles', content_rowid='rowid',
  tokenize='trigram'          -- ⭐ 中文必须用 trigram
);
```

### 6.2 两个必须讲清的设计点

**① 去重主键用 URL，不用标题也不用时间。**

URL 里的 `__biz` + `mid` + `idx` + `sn` 是永久唯一的。理由（都是实测踩过的坑）：
- 搜索接口返回的 `title` 带 `<em class="highlight">` 高亮标签，跟列表接口的标题**对不上**
- `update_time` 会在编辑后变动（实测同一篇差到 29 分钟），用时间判重会**重复推送**
- `sn` 是内容签名，内容改了它会变——这正是我们要的语义

**② 中文全文检索必须用 `tokenize='trigram'`。**

FTS5 默认的 `unicode61` 分词器对中文是**整段当一个词**，搜"宁德时代"搜不到。
`trigram` 按三字符切分，中文检索才可用。这是最容易踩的坑，写进代码注释。

**③ `published` 和 `collected` 是两个字段，不许合并。**

一个是微信的发布时间，一个是我们抓到的时刻。实测两者差 **32 分钟**。
将来看"采集延迟"、判断"有没有漏采"全靠这个差。

### 6.3 raw JSON 保留策略

**永久保留，不清理。** 一篇 10–20 KB，一年几百篇也就几 MB。
理由是本次调研的核心结论：**微信生态在持续收紧**（WeWe RSS 已归档、跨公众号列表接口
2026-07 集体报 `200013`）。任何依赖单一非官方接口的方案都要预设"半年后换实现"。
只要 raw 在，换上游后可以**无损重建**全部衍生数据，不用重抓。

---

## 7. 定时机制 —— 【延后实施，设计保留】

> ⚠️ **本节整体延后到 M6+。M1–M5 不实现任何定时能力。**

### 7.1 为什么延后

`schedule` 的命令面（`schedule set` / `install` / `run --due`）**依赖 `run` 已稳定**。
而在 M1–M5 阶段，你要的是**手动 `wxmp run` 反复试** —— 看抓出来的 Markdown 合不合用、
SQLite 检索顺不顺手。此时引入定时器只会让你**分不清"是抓取逻辑错了还是定时器没触发"**，
排查成本直接翻倍。

### 7.2 但现在就要做的兼容准备

**`config.json` 里的 `schedule` 字段照常保留、照常解析、照常校验**，
只是暂时不实现"到点自动跑"。这样将来加定时器时**不用改配置文件格式**，
你现在写的配置不会作废。

`wxmp schedule` 命令在 M1–M5 期间的行为：**读取并打印当前时刻表，修改类子命令返回
"该功能尚未实现"并以退出码 1 结束** —— 明确失败，而不是静默不生效。

### 7.3 将来实施时的方案（存档备查）

- **不内建守护进程**：常驻进程会带来开机自启、内存占用、端口冲突、崩了不知道等运维问题，
  而需求只是"每天拉两次"。
- `wxmp run --due` —— 给定时器调用，内部判断"当前是否到点"，到点就拉，否则退出 0。
- `wxmp schedule install` —— 自动生成并（经确认后）安装：
  - **Linux/WSL**：优先 `systemd --user` timer；WSL 默认没开 systemd，则回落 **crontab**
  - **macOS**：`launchd` plist
- `--dry-run` 只打印将要写入的内容，不动系统

> **WSL 坑（提前记录）**：WSL2 默认不启用 systemd，且**不跑 crontab 服务**。
> 光写 crontab 是死的。可靠做法是生成一个 **Windows 计划任务**，
> 在 Windows 侧调用 `wsl.exe -d Ubuntu -- wxmp run --due`。
> 这是 WSL 里唯一能真正跑准时的路子。

---

## 8. 采集流程（单次 `wxmp run`）

```
 1. 读 config.json（环境变量定位）
 2. 校验：token 可用？data_dir 可写？账号列表非空？
 3. 取"到点该拉"的账号集合
 4. for each account:
 4.1   POST fetch_account_articles  {username}          ← 列表，约 2.3s
 4.2   本地比对 articles.url，找出新文章                  ← 零成本
 4.3   for each 新文章:
 4.3.1     POST fetch_article_detail_h5 {url}            ← 正文，约 8.1s
 4.3.2     原子写 raw/<user>/<published>_<mid>_<idx>.json
 4.3.3     INSERT INTO articles + articles_fts
 4.4   翻页（仅当 pages>1 或首轮回采），用 next_offset
 4.5   更新 accounts.last_run_at / last_seen_url
 5. 导出新增文章的 Markdown（若 export.enabled）
 6. 写日志，退出
```

**成本控制**（本次调研的核心结论）：
- 无新文章：**1 次调用 / 2.3 秒**
- 有新文章：**2 次调用 / 约 10 秒**
- 一天两次 → 一年不到 1 块钱

**已去掉 `fetch_article_stats_h5`**：不需要互动数据后，串行依赖和
`comment_id` 性能技巧都一并消失，链路短一半。

### 8.1 健壮性要求

- **必带 `User-Agent: Mozilla/5.0`** —— 实测不带会被 Cloudflare 挡 **403**，这是硬性要求
- 指数退避重试（2s / 5s / 15s），限流 `qps: 1`
- **`next_offset` 游标不跨次保存**（base64 与当前快照绑定，隔天用会错），每次从第一页重开
- 单账号失败**不中断整轮**，记 `last_error` 继续下一个
- 所有文件写入**先写临时文件再 rename**（原子），避免断电产生半截文件

---

## 9. 导出设计（Obsidian / 局域网共享）

### 9.1 Markdown 形态

路径：`<export.dir>/<账号名>/<日期> <标题>.md`

```markdown
---
来源: 微信公众号「仓都加满」
主体: 深圳和光同行传媒有限公司
原文: http://mp.weixin.qq.com/s?__biz=...
发布: 2026-10-02 10:46
采集: 2026-10-02 11:18
账号: mtlsnow
---

（content_text 全文，关键股票名/代码已加粗）

## 来源
- 公众号：仓都加满（mtlsnow）
- 原文链接：http://mp.weixin.qq.com/s?__biz=...
```

遵守你的 Obsidian 归档规则：**标题只放文件名，正文不重复写标题**；
股票名/代码/关键观点句加粗；尽力附来源 URL。

### 9.2 局域网共享（两阶段）

**v0.1 就做**：Markdown 导出到指定目录，
你在 Windows 侧把该目录设为共享文件夹（你的 445 端口本来就是通的）。
**只共享导出目录，不共享 Obsidian vault 根** —— 否则 `.obsidian/` 配置会互相打架。

**v0.2 再说**：`export.lanshare.enabled` 对应的 Node 只读查询站。
真要做得遵守三条硬约束：绑 `192.168.5.9` 不绑 `0.0.0.0`；
SQLite 以 `mode=ro` 只读打开；**绝不能暴露到公网**。

> 我的建议：**先别做 v0.2**。为"偶尔查一下"养常驻进程，几天后你会嫌它占内存。
> 等文件共享用了两周，确实需要跨机搜索了再上。

---

## 10. 文档计划（你的要求 4）

| 文件 | 内容 |
|---|---|
| `README.md` | 5 分钟上手：装 uv → `wxmp init` → 加号 → 首次抓取 → 看结果 |
| `docs/CONFIG.md` | config.json **全字段**表：类型、默认值、是否必填、示例 |
| `docs/CLI.md` | **每个命令**的用途、参数、示例、退出码 |
| `docs/DESIGN.md` | 本稿定稿版：架构、schema、数据流 |
| `docs/TROUBLESHOOTING.md` | 403 / 401 / 账号搜不到 / 中文搜不出 / WSL 定时不跑 / 路径权限 |
| `docs/WSL.md` | WSL 专项：`/mnt/c` 性能、systemd 缺失、Windows 计划任务设置 |

---

## 11. 交付物与验收标准

### 11.1 交付物

```
outputs/wxmp/          # 完整代码 + 文档 + 测试 + 示例配置
```

### 11.2 验收标准（可逐条验）

1. `WXMP_CONFIG=/tmp/t.json wxmp init` 能在全新环境建出配置与数据目录
2. `wxmp add 仓都加满` 能搜到并**正确消歧**，写出带 `media_name` 的配置项
3. `wxmp add <url>` 能从文章 URL 解析出账号并订阅
4. `wxmp add 仓都加满 --time 08:30` 后 `cat $WXMP_CONFIG` 能看到该号时刻变更
   （写入配置即可；**定时器触发不在本期验收范围**，见 §7）
5. `wxmp run` 能抓到「仓都加满」**今天 10:46 那篇**的全文，入库
6. `wxmp search 港股` 能**命中该篇正文**（验证 FTS5 + trigram 中文检索可用）
7. 重复执行 `wxmp run` **不产生重复记录**（验证 URL 去重）
8. Markdown 导出到配置目录，文件名/来源块/加粗均符合 §9.1
9. 断网执行 `wxmp run` → 退出码 5，日志有明确错误，**不崩栈**
10. 不带 UA 的请求被拒这一坑，代码里已修（恒带 UA）
11. 全部文档齐全，`README.md` 照做能跑通

---

## 12. 已定案的决策（不用再讨论）

| # | 决策 | 结论 |
|---|---|---|
| 1 | **主语言** | **Python 3.12**（§1.1 有完整论证） |
| 2 | **HTTP 库** | **`requests`**，且锁死具体版本（§1.2） |
| 3 | **token 存放** | **`TIKHUB_TOKEN` 优先，`provider.token` 兜底**；配置字段明文存放，只需 `.gitignore` 排除 config.json |
| 4 | **平台语义** | 代码只写 POSIX（Linux/macOS），不出现 Windows 路径 |
| 5 | **config 定位** | 环境变量 `WXMP_CONFIG` 指定绝对路径，回落 `~/.config/wxmp/config.json` |
| 6 | **单一入口** | `wxmp`，所有功能挂子命令 |
| 7 | **交互数据** | 不做。上游也拿不到（实测 `read_num` 等全 null） |
| 8 | **定时调度** | **延后到 M6+**，但 config 的 `schedule` 字段现在保留 |
| 9 | **去重主键** | 文章 URL（含 `mid`+`idx`+`sn`），不用标题也不用时间 |
| 10 | **raw JSON** | 永久保留 |

---

## 12b. 仍需你决策的点（开工前需要答复）

| # | 问题 | 背景 | 我的建议 |
|---|---|---|---|
| **A** | **数据目录放哪？** | 项目代码在 `/mnt/c`（9p 协议，写 SQLite 会卡） | **数据默认落 WSL 原生** `~/.local/share/wxmp`；Markdown 导出走 `/mnt/c`（要进 Obsidian，跑不掉） |
| **B** | **默认拉取时刻？** | 实测「仓都加满」发文集中在**上午 10–11 点**（10:46 / 11:07 / 10:26 / 11:48...） | `["09:00", "16:00"]` —— 覆盖上午发文 + 下午收盘复盘 |
| **C** | **Obsidian 导出到哪个子目录？** | 你的 Research 库是私人精选，自动抓取内容混进去会稀释质量 | `Research/公众号摘录/<账号名>/`，与手动精选隔离；你精选过的再往上挪 |
| **D** | **首个订阅号就是「仓都加满」吗？** | 用来做 M3 验收的实测样本 | 是。`username=mtlsnow`，`media_name=深圳和光同行传媒有限公司` |
| **E** | **Markdown 里正文要不要保留原文的图片？** | `content_text` 是纯文本，**不含图片**；`content_noencode` 是 HTML，含 `<img>` 外链 | v0.1 先只存纯文本 + 在文末附原文链接；要图片的话 v0.2 再加（保留 HTML 或下载图到本地） |
| **F** | **文件名里的日期用哪个？** | 有"发布时间"和"采集时间"两个 | 用**发布时间**（`published`），符合直觉；采集时间写进 frontmatter |

> A、C 两项会影响目录结构，**建议优先答复**。B/D/E/F 我可以先按建议实现，你跑起来觉得不对再调。

---

## 13. 里程碑

| 阶段 | 内容 | 说明 |
|---|---|---|
| M1 | 骨架 + config + store + `init` | 可初始化、可读写库 |
| M2 | `add` / `resolve` / `list` | 订阅管理闭环（含消歧） |
| M3 | `run` 采集 + raw + FTS5 入库 | **核心可用**，含 `search` 验证中文检索 |
| M4 | `export` Markdown | 接上 Obsidian |
| M5 | `check` / `stats` / 文档完善 | 体检与文档 |
| **M6+** | **`schedule` 定时机制** | **【延后】** 见 §7 |

**建议先做 M1–M3，跑一天看真实产出，再决定 M4 之后。**

---

## 附：本次调研得出的、已写进设计的实测结论

这些不是推测，是刚刚在本机跑出来的，直接决定了设计：

| 结论 | 影响 |
|---|---|
| 不带 `User-Agent` → **403** | `api.py` 恒带 UA |
| 文章列表 **2.3s / 每次 10 条**，`page_size` 被忽略 | 翻页只能用 `next_offset` |
| 新文 **32 分钟**内已进列表 | 一天拉 2 次完全够 |
| `read_num` 等互动字段**全部 null** | 已确认不需要，直接砍掉 stats 接口 |
| 搜"仓都加满"返回 **14 条**含李鬼 | `add` 必须做消歧 + 打印主体公司 |
| 列表 `title` 与搜索 `title` 不一致（高亮标签） | 去重主键用 URL |
| `create_time` 与 `update_time` 差可达 29 分钟 | 去重绝不用时间 |
| WSL 内 sqlite **3.46.1 且 FTS5 可用** | 存储零依赖，中文用 trigram |
