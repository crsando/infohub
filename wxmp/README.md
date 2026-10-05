# wxmp

微信公众号订阅采集工具。订阅若干公众号，把新文章的**全文**抓下来存进本地 SQLite，
并导出成 Markdown。

**不做互动数据**（阅读/点赞/在看/评论）—— 上游接口也拿不到，实测这些字段全部返回 `null`。

---

## 5 分钟上手

需要 [uv](https://docs.astral.sh/uv/)，Linux / macOS / WSL 环境。

```sh
# 1. 装依赖（uv 会自动建 .venv、装 Python 3.12 与 requests）
cd wxmp
uv sync

# 2. 初始化配置
uv run wxmp init
#  → 建立 ~/.config/wxmp/config.json 与 ~/.local/share/wxmp/

# 3. 配置 TikHub API key（二选一，环境变量优先）：
#    export TIKHUB_TOKEN='你的 TikHub API key'
#    或编辑 config.json 的 provider.token
#    - 要导出 Markdown：export.enabled=true，export.dir=输出目录

# 4. 加订阅
uv run wxmp add 仓都加满                  # 按名称搜索（有歧义会要求确认）
uv run wxmp add mtlsnow --nick 仓都加满   # 按标识直接加，最快

# 5. 抓一次
uv run wxmp run

# 6. 搜一下
uv run wxmp search 南向资金
```

想要全局命令：`uv tool install .`，之后直接 `wxmp …`。下文示例都省略 `uv run`。

---

## 项目结构

```
wxmp/
├── wxmp                   # 源码树直跑入口（uv run ./wxmp …），与 uv run wxmp 等价
├── pyproject.toml         # 唯一运行时依赖：requests==2.32.3；dev 组：pytest
├── uv.lock                # 锁文件，请随代码提交
├── src/wxmp/
│   ├── cli.py             # 命令行与子命令分发
│   ├── config.py          # config.json 加载/校验/原子写回
│   ├── paths.py           # XDG 路径与 WXMP_CONFIG 解析
│   ├── api.py             # TikHub 客户端（UA、重试、限速）
│   ├── resolve.py         # 名称 / URL / 标识 → 账号（含消歧）
│   ├── pipeline.py        # 一轮采集：列表 → 去重 → 正文 → 入库
│   ├── store.py           # SQLite + FTS5，URL 归一化
│   ├── export.py          # Markdown 导出
│   └── errors.py          # 异常与退出码
├── tests/                 # 回归测试（锁住开发中踩到的坑）
├── examples/              # 最小 / 完整配置示例
├── docs/                  # 使用与设计文档（见下表）
└── dev/                   # 开发参考：TikHub 接口规格裁剪版 + 真实响应样本
```

## 开发

```sh
uv sync --group dev
uv run pytest -q
```

改上游解析逻辑前，先看 [dev/README.md](dev/README.md) 里的真实响应样本。

---

## 核心概念

### 一切由环境变量决定配置文件位置

```sh
export WXMP_CONFIG=/srv/wxmp/config.json   # 显式指定
wxmp run                                    # 读这个文件
# 未设置时回落 ~/.config/wxmp/config.json
```

数据目录同理：`WXMP_DATA_DIR`，默认 `~/.local/share/wxmp`。

### TikHub token

`TIKHUB_TOKEN` 有值时优先使用它；未设置或为空时回退到配置文件里的 `provider.token`。
环境变量只用于当前进程，不会被 `wxmp` 写回 config.json。

### 去重主键是归一化后的文章 URL

公众号文章 URL 里的 `__biz + mid + idx + sn` 是永久唯一标识。
其余参数（尤其 `chksm`）**每次请求都会变** —— 所以 wxmp 会先把 URL 归一化再比对。

> 这不是理论问题。开发期实测：不做归一化的话，同一批 10 篇文章两轮跑下来库里会有 20 行。

### 中文检索有个 3 字门槛

SQLite 的 FTS5 `trigram` 分词器**只索引 3 字符及以上的片段**，所以：

- 搜「南向资金」（4 字）→ 走 FTS5，带高亮摘要，快
- 搜「港股」（2 字）→ FTS5 会返回 0 条，wxmp 自动退回 `LIKE` 扫描

两条路都是自动选的，正常搜索不用管。代价是 2 字词会扫全表，在几千到几万篇的规模下仍是毫秒级。

---

## 命令总览

| 命令 | 用途 |
|---|---|
| `wxmp init` | 初始化配置与数据目录 |
| `wxmp add <目标>` | 新增订阅（名称 / 文章URL / 标识） |
| `wxmp remove <key>` | 移除订阅（`--purge` 连文章一起删） |
| `wxmp list` | 列出订阅 |
| `wxmp enable / disable <key>` | 启停订阅 |
| `wxmp schedule [show\|set\|unset]` | 查看/设置拉取时刻（**定时触发延后**） |
| `wxmp run` | 手动刷新一次 |
| `wxmp search <关键词>` | 全文检索 |
| `wxmp show` | 列出最近文章 |
| `wxmp export` | 重新导出 Markdown |
| `wxmp stats` | 库统计 |
| `wxmp check` | 配置与连通性体检 |

完整参数参考、退出码约定见 [docs/CLI.md](docs/CLI.md)。

---

## 退出码

脚本化调用可以靠它判断失败原因：

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 一般错误 |
| 2 | 参数错误 |
| 3 | 配置错误 |
| 4 | 凭据错误 |
| 5 | 上游错误 |
| 6 | 需要人工消歧（多候选且非交互） |

---

## 目前实现的与未实现的

**已实现（M1–M5）**：初始化、订阅管理（含消歧）、采集、raw 存档、
SQLite + FTS5 入库、中文检索、Markdown 导出、体检与统计。

**未实现**：

- **定时自动触发**（`wxmp schedule install`、`wxmp run --due`）—— 延后到 M6+。
  `config.json` 里的 `schedule` 字段现在**照常解析与保存**，将来加定时器不用改配置格式。
  现在请手动执行 `wxmp run`，或自己写条 crontab 调用它。
- 正文图片（图片地址在 HTML 的 `data-src` 而非 `src`，且 URL 里的 `&` 被转义成 `&amp;`，
  需要专门处理）。原文 HTML 已存进 raw 存档，将来想加图不用重抓。
- 局域网只读查询站。

---

## 文档

| 文件 | 内容 |
|---|---|
| [docs/CLI.md](docs/CLI.md) | 每个命令的参数、示例、退出码 |
| [docs/CONFIG.md](docs/CONFIG.md) | config.json 全字段参考 |
| [docs/DESIGN.md](docs/DESIGN.md) | 架构、存储 schema、数据流、设计取舍 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 常见错误与排查 |
| [docs/WSL.md](docs/WSL.md) | 在 WSL 里使用的注意事项 |
| [docs/RESEARCH.md](docs/RESEARCH.md) | 前期调研：监控手段、付费平台、时效性、TikHub 实测 |
| [docs/design-draft.md](docs/design-draft.md) | 开发前设计稿（历史文档，以 DESIGN.md 为准） |
| [dev/README.md](dev/README.md) | 上游接口规格与真实响应样本 |

---

## 许可

MIT
