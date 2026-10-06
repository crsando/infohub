# wxmp RSS Feed 功能

## 概述

wxmp 支持将采集的公众号文章导出为 RSS 2.0 格式的 feed，方便使用 RSS 阅读器订阅。

## 配置

在 `~/.config/wxmp/config.json` 中添加 `feed` 配置：

```json
{
  "feed": {
    "enabled": true,
    "path": "/tmp/wxmp-feed.xml",
    "max_items": 100,
    "title": "公众号订阅",
    "link": "https://mp.weixin.qq.com",
    "description": "微信公众号文章订阅",
    "per_account": false
  }
}
```

### 配置字段说明

- `enabled`: 是否启用 RSS feed 生成（默认 `false`）
- `path`: RSS 文件输出路径
  - 单文件模式：完整文件路径，如 `/path/to/feed.xml`
  - 分账号模式：输出目录或文件前缀，如 `/path/to/feeds`
- `max_items`: 每个 feed 最多包含的文章数量（默认 100）
- `title`: Feed 标题（默认 "公众号订阅"）
- `link`: Feed 链接（默认 "https://mp.weixin.qq.com"）
- `description`: Feed 描述（默认 "微信公众号文章订阅"）
- `per_account`: 是否为每个账号生成独立 feed（默认 `false`）

## 使用方法

### 1. 手动生成 feed

```bash
wxmp feed
```

### 2. 自动生成（随 run 命令）

当 `feed.enabled` 为 `true` 时，`wxmp run` 命令会在采集完成后自动生成 feed。

### 3. CLI 参数覆盖

可以通过 CLI 参数临时覆盖配置：

```bash
# 临时指定输出路径
wxmp feed --output /path/to/custom-feed.xml

# 生成分账号 feed
wxmp feed --per-account --output /path/to/feeds-dir
```

## 输出模式

### 单文件模式（默认）

`per_account: false` 时，所有账号的文章合并到一个 feed 文件：

```bash
wxmp feed
# 输出: /tmp/wxmp-feed.xml
```

### 分账号模式

`per_account: true` 时，每个账号生成独立的 feed 文件：

```bash
wxmp feed
# 输出:
#   /tmp/wxmp-feeds           (汇总 feed)
#   /tmp/wxmp-feeds-username1 (账号1的 feed)
#   /tmp/wxmp-feeds-username2 (账号2的 feed)
#   ...
```

分账号模式会生成 N+1 个文件：
- 1 个汇总 feed（路径为 `path`）
- N 个账号 feed（路径为 `path-{username}`）

## RSS Feed 内容

### Feed 结构

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>公众号订阅</title>
    <link>https://mp.weixin.qq.com</link>
    <description>微信公众号文章订阅</description>
    <language>zh-CN</language>
    <lastBuildDate>Tue, 06 Oct 2026 15:33:26 +0800</lastBuildDate>
    <item>...</item>
  </channel>
</rss>
```

### 文章条目（item）字段

每篇文章包含以下字段：

- `<title>`: 文章标题
- `<link>`: 文章链接（canonical URL）
- `<guid>`: 文章唯一标识（与 link 相同）
- `<pubDate>`: 发布时间（RFC 822 格式）
- `<author>`: 作者（公众号昵称）
- `<category>`: 分类（公众号昵称）
- `<description>`: 文章摘要
- `<content:encoded>`: 文章全文（纯文本转 HTML）

### 文本处理

- 纯文本按空行切分为段落
- 每段包装为 `<p>` 标签
- 段内换行符替换为空格
- HTML 特殊字符自动转义（`<` → `&lt;`、`>` → `&gt;`、`&` → `&amp;`）

## 示例

### 配置示例 1：单文件 feed

```json
{
  "feed": {
    "enabled": true,
    "path": "/home/user/feeds/wxmp.xml",
    "max_items": 50,
    "per_account": false
  }
}
```

生成 1 个文件：`/home/user/feeds/wxmp.xml`

### 配置示例 2：分账号 feed

```json
{
  "feed": {
    "enabled": true,
    "path": "/home/user/feeds/wxmp",
    "max_items": 100,
    "per_account": true
  }
}
```

生成 N+1 个文件：
- `/home/user/feeds/wxmp`（汇总）
- `/home/user/feeds/wxmp-account1`
- `/home/user/feeds/wxmp-account2`
- ...

## 订阅到 RSS 阅读器

### 本地文件订阅

大多数 RSS 阅读器支持 `file://` 协议：

```
file:///home/user/feeds/wxmp.xml
```

### HTTP 服务器订阅

如果通过 HTTP 服务器提供访问（如 Nginx、Apache），使用 HTTP(S) URL：

```
https://your-domain.com/feeds/wxmp.xml
```

需要确保：
1. 服务器返回正确的 Content-Type: `application/rss+xml` 或 `application/xml`
2. Feed 文件定期更新（通过 `wxmp run` 的定时任务）

## 注意事项

### 1. 文件权限

生成的 RSS 文件默认权限为 `0600`（仅所有者可读写）。如果需要通过 HTTP 服务器访问，需要调整权限：

```bash
chmod 644 /path/to/feed.xml
```

### 2. 更新频率

- 手动运行 `wxmp feed` 时立即生成
- `wxmp run` 采集后自动生成
- 建议设置定时任务（cron）定期采集和生成 feed

### 3. 文章数量限制

`max_items` 限制每个 feed 包含的文章数量。较大的值会增加 feed 文件大小和解析时间，建议根据实际需求设置（通常 50-200 篇）。

### 4. 原子写入

feed 生成使用原子写入（写入临时文件后重命名），确保 RSS 阅读器不会读取到不完整的文件。

## 实现细节

- 代码位于 `src/wxmp/feed.py`
- 测试位于 `tests/test_feed.py`
- 使用 Python 标准库 `xml.etree.ElementTree` 生成 XML
- 遵循 RSS 2.0 规范和 `content:encoded` 扩展
- 支持自动 XML 特殊字符转义
