# wxmp 设计

本文是 [design-draft.md](design-draft.md)（开发前设计稿）的定稿版，
记录了**实际实现**与设计稿的差异，以及实现过程中实测发现的新问题。

---

## 0. 定位

`wxmp` 是一个单命令入口的公众号订阅采集器：在 `config.json` 里声明订阅哪些号，
把新文章的**全文**抓下来存进本地 SQLite，并导出成 Markdown。

**不做互动数据**（阅读/点赞/在看/评论）—— 已确认不需要，且上游 H5 通道实测拿不到。

---

## 1. 技术选型

| 维度 | 选择 | 理由 |
|---|---|---|
| 主语言 | Python 3.12 | `sqlite3`(含 FTS5)、`argparse`、`json` 全在标准库，外部依赖只有 1 个 |
| HTTP | `requests==2.32.3` | 库已冻结多年不坏；比 httpx 少 2 个依赖（anyio/sniffio）；线程足够，不需要协程 |
| 存储 | SQLite + FTS5 | 单文件、可拷走；中文检索用 `tokenize='trigram'` |
| CLI | `argparse` | 标准库，不引入 click/typer |
| 测试 | `pytest` | 仅开发期需要 |

**为什么是 Python 而不是 Go/Node**：这个项目的性能瓶颈 100% 在上游响应时间
（单篇 8–17 秒），本地计算量可以忽略。真正的风险是**上游接口会变** ——
所以选型的重心是"改起来快、容错强、依赖少"，而不是"跑得快"。

---

## 2. 数据流

```
config.json
    │
    ▼
wxmp add ──→ 解析账号 ──→ config.json (accounts)
    │                      + SQLite accounts
    │
    ▼
wxmp run
    │
    ├─ 1. TikHub: fetch_account_articles   ← 每账号 1 次，约 2.3s，返回约 10 条
    ├─ 2. 本地比对 articles.url（归一化后）  ← 零成本
    ├─ 3. 只对新文章：
    │       TikHub: fetch_article_detail_h5  ← 约 9s
    │       ├─ 原子写 raw/<username>/<published>_<mid>_<idx>.json
    │       └─ INSERT INTO articles + FTS 触发器同步索引
    ├─ 4. 更新 accounts.last_run_at
    └─ 5. 导出新增文章的 Markdown
```

**成本控制**：无新文章时整轮 1 次调用 / 2.5 秒；有新文章时每篇 2 次调用 / 约 9 秒。

---

## 3. 存储 schema

```sql
accounts(username PK, user_name, nick, media_name, enabled, added_at, last_run_at, last_error)

articles(url PK, account, title, digest, content, published, collected, raw_path, exported)

-- 外部内容表 + 触发器维护，索引与 articles 同步
articles_fts USING fts5(title, content, content='articles', content_rowid='rowid',
                        tokenize='trigram')

runs(id, started_at, ended_at, account, new_count, status, message)
```

### 三个关键决定

**① 主键是归一化后的 URL**，见 §4.1。

**② `published` 与 `collected` 分开存。** 一个是微信侧发布时刻，一个是本地抓取时刻。
实测两者差 32 分钟。将来算采集延迟、判断漏采全靠这个差。

**③ 中文检索用 `trigram` 分词器。** 默认的 `unicode61` 把中文整段当一个词，
搜"宁德时代"搜不出来。trigram 按三字符切分，中文才可用 —— 但它也带来一个限制，见 §4.2。

---

## 4. 实现中实测发现的坑

设计稿写完之后才跑出来的问题，都在这里。

### 4.1 ⚠️ 上游 URL 里的 `chksm` 每次都不同 —— 必须归一化

**现象**：不做处理的话，同一批 10 篇文章，两轮下来库里是 20 行。

**原因**：上游返回的文章 URL 长这样：

```
...&mid=2247517798&idx=1&sn=2100276d8ba9...&chksm=e93e2260...&scene=126&sessionid=0#rd
                                          ^^^^^ 每次请求都不同
```

`chksm`（还有 `scene` / `sessionid`）是会话相关参数，**每次请求都变**。
把它们当主键的一部分，等于给每篇文章生成无数个"新"标识。

**解决**：`store.canonical_url()` 把 URL 归一化到只保留
`__biz + mid + idx + sn` 四个参数，按固定顺序重排，去掉 fragment。

这四个才是真正的唯一标识：`__biz` 是公众号，`mid` 是群发 ID，
`idx` 是该次群发的第几篇，`sn` 是内容签名（内容改了它也变，正是想要的语义）。

**回归测试**：`tests/test_store.py::test_不同chksm的同一篇文章去重`

### 4.2 ⚠️ trigram 分词器只索引 3 字符及以上

**现象**：搜「南向资金」（4 字）命中，搜「港股」（2 字）返回 0 条。

**原因**：FTS5 的 trigram 分词器按**三字符**为最小单位建索引，
两字符的词**根本不进索引**。这不是数据缺失，是索引不覆盖。

**解决**：`Store.search()` 按关键词长度分流：

| 长度 | 实现 | 说明 |
|---|---|---|
| ≥ 3 字 | FTS5 MATCH | 带 `snippet()` 高亮，快 |
| ≤ 2 字 | `LIKE` 扫描 | 手写摘录，全表扫描但仍是毫秒级 |

**回归测试**：`tests/test_store.py::test_检索_两字词走like兜底`

### 4.3 ⚠️ 缺 `User-Agent` 会被 Cloudflare 挡 403

设计前就实测到，已写进 `api.py` 常量。`_post()` 里对 403 单独给了提示，
避免把这个坑误诊为 token 问题。

### 4.4 ⚠️ 同一个号有两个合法标识 —— 重复订阅防护要按三个字段查

**现象**：先 `wxmp add 仓都加满`（得到自定义微信号 `mtlsnow`），
再 `wxmp add <文章URL>`（得到官方 `gh_e2899e9a812e`）—— 同一个号被订阅两次。

**原因**：`mtlsnow` 是自定义微信号，`gh_e2899e9a812e` 是官方 gh_ 号，
两者指同一个公众号。只比对 `username` 会漏判。

**解决**：`Config.add_account()` 按 `username` / `user_name` / `nick` 三个字段查重。

**回归测试**：`tests/test_config.py::test_同名不同标识的重复订阅被拦下`

### 4.5 `accounts.last_run_at` 是 TEXT 列但要存整数

SQLite 是动态类型，`mark_run()` 写入的 Unix 秒存进 TEXT 列不会报错，
但 `max()` 出来是字符串，`datetime.fromtimestamp()` 会炸。

**解决**：`stats()` 里用 `CAST(last_run_at AS INTEGER)` 取回整数。

**回归测试**：`tests/test_store.py::test_stats的last_run按整数解析`

### 4.6 中文昵称不能被误判成 username

**背景**：`wxmp add <目标>` 要区分"这是名称"还是"这是标识"。

**判据**：公众号 username 一律是 ASCII（`gh_xxx` 或自定义微信号如 `mtlsnow`），
**中文一定是昵称**。所以用 `re.fullmatch(r"[A-Za-z0-9_\-]{2,64}", value)` 判断。

**回归测试**：`tests/test_config.py::test_中文名不会被误判为username`

---

## 5. 账号消歧策略

实测搜「仓都加满」返回 14 条，去重后 5 个可用候选：

| # | 昵称 | username | 主体 |
|---|---|---|---|
| 1 | 仓都加满 | mtlsnow | 深圳和光同行传媒有限公司 |
| 2 | 仓满加仓 | cangmanjiacang | 个人 |
| 3 | 仓都加满复盘 | yanbaose | 个人 |
| 4 | 神奇软件仓 | SQRJC999 | 个人 |
| 5 | 千万仓 | zhennongyi | 上海鹰为智能科技有限公司 |

**策略**（`resolve.pick_candidate`）：

1. 只有 1 个候选 → 直接用
2. 昵称**完全相等**且只有 1 条 → 自动选定（上表场景走这条）
3. 指定了 `--media` 且只有 1 条匹配 → 选定
4. 否则 → **不猜**。交互式列出候选表让人选；非交互环境直接失败（退出码 6）

**消歧表必须打印主体公司名** —— 昵称可以被模仿，主体才是辨真伪的依据。

---

## 6. 导出设计

### Markdown 形态

```markdown
---
来源: 微信公众号「仓都加满」
主体: 深圳和光同行传媒有限公司
原文: http://mp.weixin.qq.com/s?__biz=...&mid=...&idx=1&sn=...
发布: 2026-10-02 10:46
采集: 2026-10-02 12:57
账号: mtlsnow
---

（content_text 正文）

## 来源
- 公众号：仓都加满（mtlsnow）
- 主体：深圳和光同行传媒有限公司
- 原文链接：http://mp.weixin.qq.com/s?__biz=...
- 账号标识：gh_e2899e9a812e
```

**不加 H1 标题** —— 标题只放在文件名里，正文不重复（用户的归档规则）。

### 为什么正文用 `content_text` 而不是 `content_noencode`

`content_noencode` 是 HTML，含 `<img>`，但图片地址在 **`data-src` 而非 `src`**
（微信懒加载），且 URL 里的 `&` 被转义成 `&amp;` ——
**任何标准 Markdown 转换器直接吃这段 HTML，一张图都转不出来**。

用 `content_text` 的代价是丢失原文的加粗与颜色（它是有损转换）。
**选择先保证纯文本链路可靠**：原文 HTML 已完整存进 raw 存档，
将来要加图片支持不用重抓，从存档里补就行。

### 图片方案（未实现，三选一）

| 方案 | 做法 | 代价 |
|---|---|---|
| A 纯文本（当前） | 只用 `content_text` + 文末附原文链接 | 无图、丢加粗 |
| B 图片外链 | 提 `data-src` → 还原 `&amp;` → 转 `![](...)` | 微信图床有防盗链，Obsidian 里可能裂图 |
| C 下载到本地 | 下到 `attachments/`，走相对路径 | 要管文件、处理命名冲突 |

真想要图基本只能走 C。当前选 A。

---

## 7. 定时机制 —— 未实现（延后 M6+）

`config.json` 的 `schedule` 字段**照常解析与保存**，但定时触发没实现。
`wxmp schedule install` 会**明确报"尚未实现"并返回退出码 2** ——
明确失败，而不是静默不生效。

**将来实现时要注意的坑（已记录）**：WSL2 默认不启用 systemd，
且**不跑 crontab 服务** —— 光写 crontab 是死的。
可靠做法是建 Windows 计划任务，调用 `wsl.exe -d Ubuntu -- wxmp run`。

---

## 8. 依赖策略

只有 1 个外部依赖：`requests==2.32.3`（锁死具体版本，不开区间）。

这是刻意的：项目要长期无人值守运行，升级踩雷的面越小越好。
`sqlite3` / `argparse` / `json` / `dataclasses` / `urllib` 全部标准库。

开发期额外需要：`pytest`（仅测试用，不进入运行时依赖）。
