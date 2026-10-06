# wxmp RSS 输出功能

## 概述

wxmp 可以将所有监控的公众号文章导出为 RSS 2.0 feed，方便在阅读器中订阅。

## 使用方法

### 生成 RSS feed

```bash
# 生成到指定路径
wxmp feed --output /path/to/feed.xml

# 如果配置文件中启用了 feed，run 命令会自动生成
wxmp run
```

### 配置

在 `config.json` 中添加 `feed` 配置段：

```json
{
  "feed": {
    "enabled": true,
    "path": "/srv/wxmp/feed.xml",
    "title": "公众号订阅",
    "link": "https://example.lan/wxmp/",
    "description": "我订阅的公众号文章",
    "max_items": 100,
    "per_account": false
  }
}
```

**配置项说明：**

- `enabled`: 是否在 `wxmp run` 后自动生成 feed（默认 `false`）
- `path`: feed 文件输出路径（必填）
- `title`: feed 标题
- `link`: feed 主页链接（用于 RSS 客户端显示）
- `description`: feed 描述
- `max_items`: 最多包含多少篇文章（默认 100）
- `per_account`: 是否为每个公众号单独生成一个 feed（默认 `false`）

### 对外发布

wxmp 只负责生成静态 XML 文件，对外发布需要配合 Web 服务器。

#### 使用 Caddy

```caddy
# Caddyfile
example.lan {
    root * /srv/wxmp
    file_server
}
```

#### 使用 nginx

```nginx
server {
    listen 80;
    server_name example.lan;
    root /srv/wxmp;
    location / {
        autoindex off;
    }
}
```

#### 临时预览

```bash
# 在 feed 目录中启动简单 HTTP 服务器
cd /srv/wxmp
python3 -m http.server 8080
# 访问 http://localhost:8080/feed.xml
```

## RSS 格式说明

### Feed 结构

生成的 RSS 2.0 feed 包含以下信息：

| RSS 元素 | 内容来源 |
|---|---|
| `<guid>` | 规范化的文章 URL（永久不变，避免阅读器重复推送） |
| `<link>` | 微信公众号原文链接 |
| `<title>` | 文章标题 |
| `<pubDate>` | 文章发布时间（微信侧，非采集时间） |
| `<author>` | 公众号昵称 |
| `<category>` | 公众号昵称（方便在阅读器中筛选） |
| `<description>` | 文章摘要 |
| `<content:encoded>` | 全文内容（纯文本，按段落分割） |

### 文章排序

按发布时间倒序，最新文章在前。

### 条目数量

默认最多包含最近 100 篇文章，可通过 `max_items` 配置。

## 技术细节

### 正文格式

**第一阶段（当前实现）：**纯文本

- 从 `articles.content` 字段读取纯文本
- 按空行切分段落
- 每段包装为 `<p>` 标签
- 自动转义 HTML 特殊字符

**第二阶段（计划中）：**带格式 HTML

- 从 raw JSON 读取原始 HTML
- 保留文章格式和图片
- 注意：微信图床有 Referer 校验，阅读器中可能无法显示图片

### 文件权限

wxmp 的数据目录默认为 `0700`（仅用户可读），但 RSS feed 需要被 Web 服务器读取，因此：

- `feed.path` 应该指向数据目录**之外**的位置
- feed 文件写入时使用 `0644` 权限（所有人可读）

### 更新频率

- 手动：`wxmp feed` 命令
- 自动：`wxmp run` 结束时（如果 `feed.enabled = true`）

每次生成都是完整重建，不只在有新文章时才更新，因此增删账号后也能及时反映。

## 权限和隐私

### ⚠️ 版权风险

全文转发他人公众号文章可能涉及版权问题。建议：

- 仅在内网或 VPN（Tailscale）内使用
- 添加 HTTP Basic Auth 或其他鉴权
- 使用不可猜测的 URL 路径

### Caddy 添加认证

```caddy
example.lan {
    root * /srv/wxmp
    basicauth {
        user $2a$14$hashed_password
    }
    file_server
}
```

### nginx 添加认证

```nginx
server {
    listen 80;
    server_name example.lan;
    root /srv/wxmp;
    
    auth_basic "RSS Feed";
    auth_basic_user_file /etc/nginx/.htpasswd;
    
    location / {
        autoindex off;
    }
}
```

## 按账号分组（可选）

设置 `per_account: true` 可为每个公众号单独生成 feed：

```
/srv/wxmp/
  feed.xml                    # 所有文章
  feed-account1.xml           # 账号 1 的文章
  feed-account2.xml           # 账号 2 的文章
```

在阅读器中可以选择订阅全部或单个账号。

## 故障排查

### feed 文件未生成

1. 检查 `feed.path` 路径是否有写入权限
2. 检查是否有文章数据：`wxmp export --format json --limit 1`

### 阅读器无法订阅

1. 检查 XML 格式：`xmllint --noout /path/to/feed.xml`
2. 检查 Web 服务器是否正确响应：`curl -I http://example.lan/feed.xml`
3. 确认 Content-Type 为 `application/rss+xml` 或 `application/xml`

### 文章重复

RSS 的 `<guid>` 使用规范化 URL，确保同一篇文章不会被重复推送。如果出现重复，检查 wxmp 数据库中是否有重复记录。

## 示例

### 完整配置示例

```json
{
  "accounts": [
    {"username": "account1", "nick": "公众号1"},
    {"username": "account2", "nick": "公众号2"}
  ],
  "feed": {
    "enabled": true,
    "path": "/srv/wxmp/feed.xml",
    "title": "我的公众号订阅",
    "link": "https://rss.example.lan/",
    "description": "通过 wxmp 采集的公众号文章",
    "max_items": 100,
    "per_account": false
  }
}
```

### 订阅 URL

将以下 URL 添加到你的 RSS 阅读器：

```
https://rss.example.lan/feed.xml
```

## 相关命令

- `wxmp run` - 采集文章，自动生成 feed（如果启用）
- `wxmp feed` - 手动生成 feed
- `wxmp export` - 导出文章（JSON/Markdown 格式）
