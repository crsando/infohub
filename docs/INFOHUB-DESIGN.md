# infohub 统一入口设计方案

> 状态：MVP 已实现。本文件同时作为实现契约；未完成的增强项仍保留在分阶段计划中。
>
> 目标：保留 `wxmp` 和 `xnews` 的独立性，增加一个根目录 CLI `infohub`，统一采集增量、调用本地 Qwen 生成摘要，并把摘要幂等写入本机 Docker 部署的 Memos。

## 1. 目标与边界

### 1.1 目标

- 一个命令编排微信公众号和 X/Twitter 两个采集器。
- 只处理源项目新发现或内容发生变化的条目。
- 使用统一提示词调用 OpenAI-compatible Qwen 接口生成中文摘要。
- 保存源条目、内容哈希、摘要版本、调用状态和 Memos 发布状态。
- 在 Memos 中形成按发布时间排序的资讯 timeline。
- 失败可重试，重试不产生重复 memo。
- 全部配置由文件和环境变量驱动，token 不进入 Git 或普通日志。

### 1.2 非目标

- 不把 `wxmp` 和 `xnews` 的数据库强行合并。
- 不在第一版实现 Memos 的完整编辑器、权限管理或 Web 前端。
- 不把模型的 reasoning 内容发布给 Memos，也不把模型摘要当作投资建议。
- 不保证上游接口的实时推送；第一版以单轮 `run` 为主，定时由外部任务触发。

## 2. 核心判断

`wxmp` 和 `xnews` 已经拥有各自的上游适配、原始响应、去重和 Markdown 导出逻辑。`infohub` 应作为编排层和索引层存在：

```text
wxmp run ─────┐
              ├─ 只读读取源 SQLite ──> infohub timeline.db
xnews run ────┘                              │
                                            ▼
                                  Qwen 摘要队列
                                            │
                                            ▼
                                      Memos API
```

这样做的原因是：源项目可以独立升级和手工运行，统一入口只依赖稳定的 SQLite 表和标准 CLI，不需要把两个项目的内部模块、依赖和数据库事务耦合在一起。

## 3. 目录和组件

建议在当前 Git 仓库根目录增加根项目，而不是把代码嵌套到 `wxmp/` 或 `xnews/`：

```text
infohub/                         # 当前仓库根目录
├── pyproject.toml               # 根 CLI，命令名 infohub
├── uv.lock
├── src/infohub/
│   ├── cli.py                   # init/check/run/ingest/summarize/publish/status
│   ├── config.py                # 配置读取、校验和环境变量优先级
│   ├── paths.py                 # 配置、timeline.db、raw 和日志路径
│   ├── sources.py               # wxmp/xnews 子进程编排和 SQLite 只读适配器
│   ├── ingest.py                # 源数据标准化、哈希、增量入库
│   ├── llm.py                   # OpenAI-compatible Chat Completions 客户端
│   ├── memos.py                 # Memos API 适配和幂等发布
│   ├── store.py                 # timeline.db schema、队列和状态迁移
│   ├── render.py                # Memos memo 内容和本地 Markdown 视图
│   └── errors.py
├── docs/
│   ├── INFOHUB-DESIGN.md
│   └── LLM-SUMMARY.md
├── wxmp/                        # 保持现有独立子项目
└── xnews/                       # 保持现有独立子项目
```

运行时默认目录：

```text
~/.config/infohub/config.json
~/.local/share/infohub/timeline.db
~/.local/share/infohub/raw/llm/
~/.local/share/infohub/logs/
```

这些路径都应可用 `INFOHUB_CONFIG`、`INFOHUB_DATA_DIR` 覆盖。目录权限 `0700`，数据库、配置、raw 和日志文件默认 `0600`。

## 4. 配置方案

配置文件不保存任何 token：

```json
{
  "version": 1,
  "sources": {
    "wxmp": {
      "enabled": true,
      "config": "~/.config/wxmp/config.json",
      "database": "~/.local/share/wxmp/wxmp.db",
      "run_command": ["uv", "run", "--directory", "wxmp", "wxmp", "run"]
    },
    "xnews": {
      "enabled": true,
      "config": "~/.config/xnews/config.json",
      "database": "~/.local/share/xnews/xnews.db",
      "run_command": ["uv", "run", "--directory", "xnews", "xnews", "run"]
    }
  },
  "llm": {
    "base_url": "http://192.168.5.17:8000/v1",
    "model": "incoai/Qwen3.8-27B-Splash",
    "api_key_env": "LLM_API_KEY",
    "timeout_seconds": 180,
    "temperature": 0.1,
    "max_tokens": 700,
    "max_input_chars": 24000,
    "chat_template_kwargs": {
      "enable_thinking": false
    },
    "prompt_version": "summary-v1",
    "system_prompt": "你是中文资讯编辑。请严格依据原文，提炼 2-4 条简短要点。只陈述原文明确表达的事实、作者判断或预测，不补充常识，不猜测缺失信息。保留重要主体、数字、时间和条件；不要给出投资建议或风险评级。",
    "user_prompt_template": "平台：{source}\n作者：{author}\n标题：{title}\n发布时间：{published_at}\n原文链接：{source_url}\n正文：\n{body}\n\n请输出简短中文摘要，只保留 2-4 条要点。"
  },
  "memos": {
    "base_url": "http://127.0.0.1:5230",
    "token_env": "MEMOS_TOKEN",
    "visibility": "PRIVATE",
    "endpoint": "/api/v1/memos",
    "timeout_seconds": 30,
    "tags": ["infohub"]
  },
  "pipeline": {
    "run_sources": true,
    "summarize_new_only": true,
    "publish_to_memos": true,
    "max_items_per_run": 100,
    "retry_failed": true
  }
}
```

### 4.1 凭据优先级

- TiKHub：沿用两个子项目的规则，`TIKHUB_TOKEN` 优先于各自配置文件中的 `provider.token`。
- Qwen：如果接口启用认证，读取 `LLM_API_KEY`（配置的 `api_key_env` 只保存变量名）；空值表示不带认证头。
- Memos：读取 `MEMOS_TOKEN`；不能把 token 写入 `config.json`。
- 运行时可用 `LLM_BASE_URL` 和 `MEMOS_URL` 覆盖配置文件中的服务地址；`MEMOS_URL` 会自动去掉尾部 `/`。

所有 token 都不得进入命令行参数、raw JSON、SQLite 内容字段、Memos memo 正文或日志。

### 4.2 Memos 配置和 Docker

当前机器已有通过 Docker 暴露 5230 端口的 Memos 部署。infohub 只依赖 HTTP API，不管理容器生命周期。推荐把 `base_url` 配成 `http://127.0.0.1:5230`，并用以下命令确认服务可访问：

```bash
curl --fail --max-time 10 "$MEMOS_URL/api/v1/memos?pageSize=1" \
  -H "Authorization: Bearer $MEMOS_TOKEN"
```

`base_url` 在客户端初始化时统一去掉尾部 `/`，避免生成 `//api/v1/memos`。Memos API 版本可能变化，路径、认证和请求字段集中在 `memos.py`，其他组件不能拼接 URL 或依赖 Memos 响应的内部字段。

## 5. 增量数据模型

源数据库继续作为原文和 raw 的权威来源，`timeline.db` 只保存统一索引、摘要和投递状态。

```sql
CREATE TABLE items (
  item_key TEXT PRIMARY KEY,                 -- wxmp:<canonical_url> / xnews:<post_id>
  source TEXT NOT NULL,                      -- wxmp 或 xnews
  source_id TEXT NOT NULL,
  author TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL,
  source_url TEXT NOT NULL,
  published_at INTEGER,
  collected_at INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  raw_path TEXT,
  markdown_path TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(source, source_id)
);

CREATE TABLE summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_key TEXT NOT NULL REFERENCES items(item_key),
  content_hash TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  model TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  response_path TEXT,
  input_chars INTEGER NOT NULL,
  duration_ms INTEGER NOT NULL,
  usage_json TEXT,
  status TEXT NOT NULL,                       -- pending/running/ok/failed
  error TEXT,
  created_at INTEGER NOT NULL,
  UNIQUE(item_key, content_hash, prompt_version, model)
);

CREATE TABLE deliveries (
  item_key TEXT NOT NULL REFERENCES items(item_key),
  summary_id INTEGER NOT NULL REFERENCES summaries(id),
  target TEXT NOT NULL,                       -- memos
  remote_name TEXT,
  remote_uid TEXT,
  body_hash TEXT NOT NULL,
  status TEXT NOT NULL,                       -- pending/published/failed
  error TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  published_at INTEGER,
  PRIMARY KEY(item_key, summary_id, target)
);

CREATE TABLE runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at INTEGER NOT NULL,
  ended_at INTEGER,
  source_status_json TEXT,
  ingested_count INTEGER NOT NULL DEFAULT 0,
  summarized_count INTEGER NOT NULL DEFAULT 0,
  published_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  error TEXT
);
```

`item_key` 的稳定性依赖源项目已经实现的去重规则：微信公众号使用 canonical URL，X 使用数字帖子 ID。`items` 每个稳定 ID 只保留当前正文快照，`content_hash` 变化时在 `summaries` 创建新的摘要版本；内容和提示词版本都未变化时直接跳过模型调用。源数据库和 raw 文件仍是原始内容的权威来源。

## 6. 运行流程

### 6.1 单轮 `infohub run`

```text
1. 校验配置、目录、TiKHub 凭据、Qwen /v1/models 和 Memos 连通性。
2. 依次运行启用的 wxmp / xnews 子项目；记录退出码和输出摘要。
3. 只读连接两个源 SQLite，按稳定 ID 和 content_hash 导入 timeline.db。
4. 对没有当前摘要版本的 item 入 summaries.pending。
5. 按发布时间升序处理摘要队列，调用 Qwen。
6. 只把非空 summary 渲染为 memo，调用 Memos API。
7. 记录远端 memo 标识、请求体哈希和发布时间。
8. 输出本轮新增、摘要成功/失败、Memos 发布成功/失败和耗时。
```

源任务建议默认串行，避免同时触发两个上游 API；`--source wxmp`、`--source xnews` 可单独运行。后续若要并行，只并行源采集，不并行同一 provider 的请求。

### 6.2 独立子命令

```text
infohub init                         创建配置和目录
infohub check                        检查配置、权限和数据库
infohub check --llm                  检查 Qwen /v1/models
infohub check --memos                检查 Memos API 和 token
infohub run                          采集、增量入库、摘要、发布
infohub ingest                       只同步两个源数据库
infohub summarize --limit 20         只处理摘要队列
infohub publish --limit 20           只发布已完成摘要
infohub status                       查看队列和失败记录
```

第一版只实现单轮命令。定时执行可以先由 systemd timer、cron 或外部任务调用 `infohub run`，避免在 CLI 内维护常驻调度器。

## 7. Qwen 摘要设计

### 7.1 请求方式

统一调用：`POST {llm.base_url}/chat/completions`。提示词从 `llm.system_prompt` 和 `llm.user_prompt_template` 读取，不硬编码在客户端。请求顶层必须包含：

```json
"chat_template_kwargs": {"enable_thinking": false}
```

其他默认值：`temperature=0.1`、`max_tokens=700`。完整接口模板见 [LLM-SUMMARY.md](LLM-SUMMARY.md)。

### 7.2 提示词

配置文件中的 `system_prompt` 是系统消息，`user_prompt_template` 是用户消息模板。用户模板支持以下变量：

```text
{source} {author} {title} {published_at} {source_url} {body}
```

默认系统提示词：

```text
你是中文资讯编辑。请严格依据原文，提炼 2-4 条简短要点。
只陈述原文明确表达的事实、作者判断或预测，不补充常识，不猜测缺失信息。
保留重要主体、数字、时间和条件；不要给出投资建议或风险评级。
```

默认用户模板包含平台、作者、标题（X 没有标题时由首句生成）、发布时间、原文链接和正文。模型输出只取 `message.content`，永远不把 `reasoning_content` 发布到 Memos。修改提示词后应同步递增 `llm.prompt_version`，这样已有摘要会按新版本重新排队。

### 7.3 长文、空响应和重试

- `max_input_chars` 默认 24,000；超长正文在本地记录原文长度，并采用可配置的截取/分块策略，不修改源数据库。
- 首次响应 HTTP 成功但 `content` 为空，记录 usage 和 reasoning 长度，有限重试一次；仍为空则标记 `failed`。
- 网络错误、429、5xx 使用有限退避；4xx 配置或认证错误不盲目重试。
- 失败条目留在队列中，下一轮可以用 `summarize --retry-failed` 重试。
- 只有非空摘要能进入 publish 队列。

由于实测服务端仍返回 reasoning，不能仅凭 `enable_thinking=false` 判断已关闭 thinking；必须检查实际响应和 usage。

## 8. Memos 发布和幂等

### 8.1 Memo 内容

每篇资讯生成一个 memo，格式类似：

```markdown
[人民币资产：价值重估的新阶段](https://mp.weixin.qq.com/s/example)

来源：微信公众号 · 李佳图 · 2026-10-03 12:00

- 人民币在美元走强背景下表现偏强，一度升破 6.70。
- 文章将人民币强势与经常账户/GDP 比率升至高位联系起来。

#infohub #wxmp
```

X 帖子使用 `X/Twitter · 作者 · 发布时间` 作为来源行。标签由配置追加，正文不包含模型 reasoning 或原始完整文章。

### 8.2 去重策略

本地 `deliveries` 是第一层幂等保护：同一 `item_key + summary_id + target` 已是 `published` 就跳过。请求体生成后计算 `body_hash`，用于检测模板变化。

为处理“远端创建成功、本地写状态前进程崩溃”的窗口，当前版本不依赖远端正文查重；当前 Memos 列表接口也不保证强一致。创建成功后立即保存本地 `remote_name/remote_uid`，出现异常时需要人工检查，不能无条件重复创建。

源内容发生变化时，生成新的 `summary_id` 和新的 memo，并在本地把旧 delivery 标记为 `superseded`；第一版不依赖 Memos 的版本化编辑 API。

### 8.3 Memos HTTP 契约

当前适配器按 Memos v1 风格 API 设计，创建请求为：

```http
POST /api/v1/memos
Authorization: Bearer <MEMOS_TOKEN>
Content-Type: application/json
```

请求体最小包含：

```json
{
  "content": "渲染后的 Markdown memo",
  "visibility": "PRIVATE"
}
```

`visibility` 从配置读取，不在摘要正文中硬编码。响应中的 `name`、`uid` 或等价远端标识写入 `deliveries`；如果具体 Memos 版本要求额外的 `creatorId`、`resourceList` 或不同认证方式，只修改 `memos.py` 适配器和配置，不扩散到 pipeline。发布前先调用健康检查和最小权限验证，禁止把 token 放到 URL。

实测当前本机 Memos：创建接口返回 `200` 和完整的 `name/uid`，但列表接口会忽略部分筛选条件，刚创建的私有 memo 也不一定立即出现在列表中。因此列表查询只能作为崩溃后的尽力对账手段，不能替代本地 `deliveries` 状态；创建成功后必须立即保存远端标识。

## 9. 错误处理和可观测性

每一层都记录状态而不是吞掉异常：

| 层 | 失败记录 | 后续动作 |
| --- | --- | --- |
| 源采集 | 子进程退出码、脱敏 stderr | 其他源继续；本轮标记 partial |
| SQLite 导入 | item key、数据库路径和异常 | 不删除已有数据，下轮重试 |
| Qwen | HTTP、耗时、usage、响应摘要（不含 token） | 有限重试，失败留队列 |
| Memos | HTTP、脱敏错误、请求体 hash | 不丢摘要，下轮 publish 重试 |

日志不能包含 `Authorization`、TiKHub token、Memos token、LLM API key 或完整原文。raw LLM 响应如果开启保存，路径放在 infohub data 目录，权限 `0600`。

## 10. 测试计划

### 单元测试

- 配置默认值、环境变量优先级、无 token 和权限修复。
- wxmp / xnews 两种源记录转换、稳定 ID、内容哈希和重复导入。
- SQLite 状态迁移、摘要版本唯一约束和失败重试。
- Qwen 请求体包含 `chat_template_kwargs.enable_thinking=false`，并正确提取 `message.content`。
- 空 content、reasoning 占满预算、429/5xx、超时等响应处理。
- Memos 请求 URL 去尾 `/`、鉴权头、body 渲染和重复发布保护。

### 手工 smoke test

```bash
uv run infohub check --llm
uv run infohub check --memos
uv run infohub run --dry-run
uv run infohub ingest
uv run infohub summarize --limit 1
uv run infohub publish --limit 1
```

真实 smoke test 必须使用环境变量提供 token，测试 memo 使用明确的 `#infohub-test` 标签，验证完成后可按远端 UID 删除，避免污染正式 timeline。

## 11. 分阶段实施

1. **M1：根 CLI 和配置**：建立 `pyproject.toml`、配置加载、权限、`check`。
2. **M2：增量索引**：读取两个源 SQLite，建立 `timeline.db` 和 `ingest`。
3. **M3：Qwen 客户端**：加入提示词、响应校验、usage 记录、失败重试。
4. **M4：Memos 适配器**：实现连通性检查、幂等标记和 `publish`。
5. **M5：统一 `run` 和测试**：串联流程，加入 dry-run、状态输出和真实接口 smoke test。
6. **M6：定时和展示增强**：按实际运行结果决定是否增加 watcher、搜索和 Web timeline。

每个阶段都保留源项目原有 CLI 可独立运行；infohub 出问题时，不影响原始采集和 Markdown/raw 归档。
