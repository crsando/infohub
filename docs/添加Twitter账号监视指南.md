# 添加 Twitter/X 账号监视指南

## 前置准备

### 1. 获取 TiKHub Token

xnews 使用 TiKHub API 获取 Twitter/X 数据，需要先获取 API Token。

访问 [TiKHub](https://api.tikhub.io/) 注册并获取 API Token。

### 2. 设置环境变量

```bash
export TIKHUB_TOKEN='你的_TiKHub_token'
```

建议写入 `~/.bashrc` 或 `~/.zshrc` 以便持久化：

```bash
echo "export TIKHUB_TOKEN='你的_TiKHub_token'" >> ~/.bashrc
source ~/.bashrc
```

---

## 初始化 xnews

首次使用需要初始化配置：

```bash
cd /home/ralmia/src/infohub/xnews
uv run xnews init
```

这会创建：
- 配置文件：`~/.config/xnews/config.json`
- 数据目录：`~/.local/share/xnews/`
  - `timeline.db` - SQLite 数据库
  - `raw/` - 原始 JSON 响应
  - `markdown/` - 导出的 Markdown 文件

---

## 添加监视账号

### 方式 1：通过用户名添加（推荐）

```bash
uv run xnews add elonmusk
```

xnews 会自动：
1. 通过 TiKHub 解析用户名 → 获取 rest_id（Twitter 内部 ID）
2. 获取最新帖子并保存到数据库
3. 将账号添加到配置文件

### 方式 2：添加但不立即抓取

```bash
uv run xnews add MacroMargin --no-fetch
```

稍后运行 `xnews run` 时会抓取。

### 方式 3：指定别名和抓取参数

```bash
uv run xnews add paulg \
  --nick "Paul Graham" \
  --pages 3 \
  --max-posts 100
```

**参数说明：**
- `--key KEY` - 配置中的键名（默认使用用户名）
- `--nick NICK` - 显示别名（默认从 API 获取）
- `--pages PAGES` - 每次抓取页数（默认 1）
- `--max-posts MAX_POSTS` - 每次最多抓取帖子数（默认无限制）
- `--no-fetch` - 只添加配置，不立即抓取

---

## 管理监视账号

### 查看已添加的账号

```bash
uv run xnews list
```

输出示例：
```
elonmusk (Elon Musk) - 启用
MacroMargin (Macro Margin) - 停用
paulg (Paul Graham) - 启用
```

### 停用账号（不删除数据）

```bash
uv run xnews disable elonmusk
```

### 重新启用账号

```bash
uv run xnews enable elonmusk
```

### 删除账号

```bash
uv run xnews remove elonmusk
```

**注意：** 这只删除配置中的账号，不删除已抓取的数据。

---

## 运行采集

### 手动运行一次

```bash
uv run xnews run
```

这会：
1. 遍历所有启用的账号
2. 抓取每个账号的最新帖子
3. 去重并保存到数据库
4. 保存原始 JSON 到 `raw/` 目录

### 持续监视（轮询模式）

```bash
uv run xnews watch --interval 300
```

每 300 秒（5 分钟）运行一次采集。

### 只抓取特定账号

```bash
uv run xnews run --only elonmusk
```

---

## 查看和搜索

### 查看最近帖子

```bash
uv run xnews show --limit 10
```

### 全文搜索

```bash
uv run xnews search "AI safety"
```

搜索会在帖子正文和用户信息中查找关键词。

### 查看统计信息

```bash
uv run xnews stats
```

输出示例：
```json
{
  "accounts": 3,
  "posts": 1247,
  "last_run": "2026-10-05T14:32:15Z"
}
```

---

## 导出 Markdown

### 导出所有帖子

```bash
uv run xnews export --all
```

Markdown 文件会保存到 `~/.local/share/xnews/markdown/`。

### 导出特定账号

```bash
uv run xnews export --account elonmusk
```

### 导出特定时间范围

```bash
uv run xnews export --since 2026-10-01 --until 2026-10-05
```

---

## 配置文件说明

配置文件位于 `~/.config/xnews/config.json`：

```json
{
  "accounts": {
    "elonmusk": {
      "rest_id": "44196397",
      "screen_name": "elonmusk",
      "display_name": "Elon Musk",
      "enabled": true,
      "pages": 1,
      "max_posts": null
    },
    "paulg": {
      "rest_id": "183749519",
      "screen_name": "paulg",
      "display_name": "Paul Graham",
      "enabled": true,
      "pages": 3,
      "max_posts": 100
    }
  },
  "fetch": {
    "timeout": 30,
    "retries": 3,
    "rate_limit_delay": 5
  },
  "export": {
    "dir": "~/.local/share/xnews/markdown",
    "format": "yaml-frontmatter"
  }
}
```

**字段说明：**
- `rest_id` - Twitter 内部用户 ID（稳定，不会变）
- `screen_name` - 用户名（可能会被用户修改）
- `display_name` - 显示名称
- `enabled` - 是否启用
- `pages` - 每次抓取页数（1 页约 20 条）
- `max_posts` - 每次最多抓取帖子数

可以手动编辑此文件，但建议通过 CLI 命令管理。

---

## 与 infohub 集成

xnews 的数据库会被 infohub 读取并整合：

```bash
# 在 infohub 中配置 xnews 数据源
cd /home/ralmia/src/infohub
uv run infohub check
```

infohub 配置文件 `~/.config/infohub/config.json` 中：

```json
{
  "sources": {
    "xnews": {
      "enabled": true,
      "database": "~/.local/share/xnews/timeline.db",
      "run_command": null
    }
  }
}
```

运行 infohub 时，会自动从 xnews 数据库读取新帖子，调用 LLM 生成摘要，并发布到 Memos。

---

## 常见问题

### Q: 如何批量添加多个账号？

A: 写一个脚本：

```bash
#!/bin/bash
accounts=(
  "elonmusk"
  "paulg"
  "naval"
  "sama"
  "balajis"
)

for account in "${accounts[@]}"; do
  uv run xnews add "$account" --no-fetch
done

# 统一抓取
uv run xnews run
```

### Q: 如何修改抓取频率？

A: 编辑配置文件中的 `fetch.rate_limit_delay`，或使用 `xnews watch --interval <秒数>`。

### Q: 如何重新解析已抓取的数据？

A: 使用 `reparse` 命令：

```bash
uv run xnews reparse --all
```

这会从 `raw/` 目录读取原始 JSON 并重新解析。

### Q: 如何查看某个账号的抓取状态？

A: 使用 `check` 命令：

```bash
uv run xnews check
```

### Q: Token 失效了怎么办？

A: 更新环境变量：

```bash
export TIKHUB_TOKEN='新的_token'
uv run xnews check  # 验证
```

---

## 完整工作流示例

```bash
# 1. 初始化
cd /home/ralmia/src/infohub/xnews
export TIKHUB_TOKEN='你的_token'
uv run xnews init

# 2. 添加监视账号
uv run xnews add elonmusk
uv run xnews add paulg --pages 3
uv run xnews add naval --no-fetch

# 3. 运行采集
uv run xnews run

# 4. 查看结果
uv run xnews show --limit 5
uv run xnews stats

# 5. 搜索
uv run xnews search "artificial intelligence"

# 6. 导出
uv run xnews export --all

# 7. 集成到 infohub
cd /home/ralmia/src/infohub
uv run infohub run --source xnews
```

---

## 进阶：自动化运行

### 使用 systemd timer（推荐）

创建 `~/.config/systemd/user/xnews.service`：

```ini
[Unit]
Description=xnews Twitter monitor
After=network.target

[Service]
Type=oneshot
WorkingDirectory=/home/ralmia/src/infohub/xnews
Environment="TIKHUB_TOKEN=你的_token"
ExecStart=/home/ralmia/.local/bin/uv run xnews run
```

创建 `~/.config/systemd/user/xnews.timer`：

```ini
[Unit]
Description=Run xnews every 5 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

启用：

```bash
systemctl --user daemon-reload
systemctl --user enable --now xnews.timer
systemctl --user status xnews.timer
```

### 使用 cron

```bash
crontab -e
```

添加：

```cron
*/5 * * * * cd /home/ralmia/src/infohub/xnews && TIKHUB_TOKEN='你的_token' /home/ralmia/.local/bin/uv run xnews run >> /tmp/xnews.log 2>&1
```

---

需要帮助？查看详细文档：
- xnews 设计文档：`/home/ralmia/src/infohub/xnews/docs/DESIGN.md`
- infohub 集成文档：`/home/ralmia/src/infohub/README.md`
