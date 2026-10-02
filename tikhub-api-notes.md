# TiKHub API 研究笔记：微信公众号、微博、X/Twitter

> 研究日期：2026-10-02
> 研究范围：TiKHub 的统一调用方式，以及微信公众号（文章/账号）、微博、X/Twitter 的读取型接口。
> 写作时受网络限制，未拉取实时 OpenAPI 或实测 API Key；路径/字段示例需要以当前文档核对。后续复制来的 `wxmp/dev/openapi-wechat.json` 含公众号接口的裁剪版规格。

## 1. 先看结论

- TiKHub 更适合作为“多平台数据采集网关”：调用方使用一个 TiKHub API Key，TiKHub 负责对接各平台的 Web/App 数据源，并把结果包装成 JSON。
- 集成时最重要的三个变量是：base URL、认证头、文档中给出的 operation path。通常入口是 https://api.tikhub.io，版本路径常见为 /api/v1/...；最终以账户页面和 OpenAPI 文档为准。
- 三个平台都应先解决**标识符归一化**再抓内容：公众号使用 biz/fakeid/账号标识 或文章 URL，微博使用 uid/mid，X/Twitter 使用 user_id/tweet_id。显示名称、短用户名和标题都不适合作为长期主键。
- 公众号文章和微博/X 的公开读取接口与官方开放平台接口不是同一个权限模型。TiKHub 可能依赖其维护的 Web/App 会话、Cookie 或上游接口，字段和可用性会随平台改版变化。
- 生产程序应该保存原始响应、文档版本/抓取时间和分页游标；不要只保存整理后的标题和正文。

## 2. 文档入口和阅读方法

以下入口按优先级尝试。若其中一个跳转到登录页，以 TiKHub 控制台显示的 API Host 为准。

| 用途 | URL |
| --- | --- |
| 产品首页/控制台 | https://tikhub.io/ |
| 文档站（常见入口） | https://docs.tikhub.io/ |
| Swagger UI（常见入口） | https://api.tikhub.io/docs |
| ReDoc（常见入口） | https://api.tikhub.io/redoc |
| OpenAPI JSON（常见入口） | https://api.tikhub.io/openapi.json |

如果拿到了 Key，可以先把 OpenAPI 保存下来，再搜索平台标签和路径：

~~~bash
export TIKHUB_API_KEY='替换成控制台中的 Key'
curl -fsS --connect-timeout 10 --max-time 30 \
  -H "Authorization: Bearer $TIKHUB_API_KEY" \
  -H 'Accept: application/json' \
  'https://api.tikhub.io/openapi.json' > tikhub-openapi.json

# jq 可用时，列出三个目标平台的 path
jq -r '.paths | keys[] | select(test("wechat|weixin|weibo|twitter|x"; "i"))' \
  tikhub-openapi.json
~~~

文档中需要逐项记录：

1. servers 中的真实 API Host 和版本前缀。
2. 认证方式是 Authorization: Bearer、X-API-Key 还是页面规定的其他头；不要凭经验混用。
3. 每个 operation 的 HTTP 方法、必填参数、参数位置（query/path/body/header）、分页字段和响应示例。
4. 是否需要平台 Cookie、设备参数、代理或登录态，以及这些凭据的保存期限。
5. 计费单位、并发/速率限制、超时和错误码。

## 3. 统一请求模型

### 3.1 cURL 模板

下面只固定通用部分；TIKHUB_PATH 和查询参数必须从当前文档复制。

~~~bash
export TIKHUB_API_KEY='替换成控制台中的 Key'
export TIKHUB_BASE_URL='https://api.tikhub.io'

curl --fail-with-body --retry 2 --retry-delay 1 \
  --connect-timeout 10 --max-time 60 \
  -H "Authorization: Bearer $TIKHUB_API_KEY" \
  -H 'Accept: application/json' \
  "$TIKHUB_BASE_URL$TIKHUB_PATH?$TIKHUB_QUERY"
~~~

若文档要求 JSON body：

~~~bash
curl --fail-with-body --retry 2 --connect-timeout 10 \
  -X POST \
  -H "Authorization: Bearer $TIKHUB_API_KEY" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  "$TIKHUB_BASE_URL$TIKHUB_PATH" \
  --data '{"keyword":"示例","page":1,"count":20}'
~~~

### 3.2 Python 最小客户端

~~~python
import os
import time
import requests

BASE_URL = os.getenv("TIKHUB_BASE_URL", "https://api.tikhub.io")
API_KEY = os.environ["TIKHUB_API_KEY"]


def tikhub_get(path: str, params: dict, *, timeout: int = 60) -> dict:
    response = requests.get(
        f"{BASE_URL.rstrip('/')}/{path.lstrip('/')}",
        params=params,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json",
        },
        timeout=timeout,
    )
    # 先检查 HTTP，再检查 TiKHub 自己的业务码。
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("code") not in (None, 0, 200):
        raise RuntimeError(
            f"TiKHub error: code={payload.get('code')}, "
            f"message={payload.get('message') or payload.get('msg')}"
        )
    return payload


def backoff_sleep(attempt: int) -> None:
    time.sleep(min(2 ** attempt, 30))
~~~

code 的成功值要以文档为准。上面的 (0, 200) 只是兼容常见包装格式，不应据此推断 TiKHub 的正式错误码定义。

### 3.3 分页和错误处理

- 常见分页形态有 page/count、offset/limit、since_id/max_id 和 cursor/next_cursor。每个平台、甚至同一平台的不同 operation 可能不同。
- 将请求参数和返回的游标一起持久化。next_cursor 为 0、空字符串或 null 时，先按文档判断是否结束，不要无条件继续请求。
- 401/403：Key、套餐、平台会话或权限问题；先停止重试并检查控制台配置。
- 404/422：路径、资源 ID 或必填参数错误；修正请求后再试。
- 429：读取 Retry-After（如果有），用指数退避；不要并发放大重试。
- 5xx、连接超时：有限次数重试并记录 request id/响应体摘要；原始响应不要丢失。

## 4. 微信公众号（WeChat Official Account）

### 4.1 典型能力和查文档关键词

TiKHub 文档可能把这一组标为 WeChat、Weixin、Official Account 或 MP。常见的 URL 形态是 /api/v1/wechat/mp/{operation}，但实际 operation 名称需要从当前 OpenAPI 的 paths 复制。

| 目标 | 需要在文档中寻找的 operation 关键词 | 首选入参 |
| --- | --- | --- |
| 搜索公众号 | search、account、official account、mp | keyword；可能还有 page/offset、count/limit |
| 读取账号文章列表 | article list、history、posts | biz/fakeid/账号 ID；分页字段 |
| 读取文章详情 | article detail、content、article | 文章 URL 或 article_id/mid/idx |
| 解析文章 URL | parse、resolve、article url | url 或 article_url |

### 4.2 推荐调用顺序

1. 用账号名称或微信号搜索，保存返回的稳定账号标识（可能叫 biz、fakeid、gh_id 或其他名字）。
2. 用该标识请求文章列表；保存文章的标题、发布时间、摘要、封面、文章 URL 和分页游标。
3. 对需要全文的条目，再用文章 URL 或文章 ID 请求详情。
4. 将 content_html、纯文本、图片 URL 和原始 JSON 分开存储。公众号文章中的图片常是延迟加载或带临时签名，不能只依赖 HTML 中的普通 src。

列表请求的改写模板：

~~~bash
# TIKHUB_PATH 从文档复制，例如 /api/v1/wechat/mp/<article-list-operation>
export TIKHUB_PATH='/api/v1/wechat/mp/<article-list-operation>'
export TIKHUB_QUERY='biz=<账号标识>&page=1&count=20'

curl --fail-with-body \
  -H "Authorization: Bearer $TIKHUB_API_KEY" \
  -H 'Accept: application/json' \
  "$TIKHUB_BASE_URL$TIKHUB_PATH?$TIKHUB_QUERY"
~~~

详情请求的改写模板：

~~~bash
export TIKHUB_PATH='/api/v1/wechat/mp/<article-detail-operation>'
export TIKHUB_QUERY='url=https%3A%2F%2Fmp.weixin.qq.com%2Fs%2F<article>'

curl --fail-with-body \
  -H "Authorization: Bearer $TIKHUB_API_KEY" \
  -H 'Accept: application/json' \
  "$TIKHUB_BASE_URL$TIKHUB_PATH?$TIKHUB_QUERY"
~~~

### 4.3 公众号的实际风险

- biz/fakeid 等标识不能通过名称猜测；搜索结果可能重复或变化，应该保存账号名称、别名和头像用于复核。
- 账号搜索、历史文章列表和全文解析的上游接口可能不是同一个接口，权限和稳定性也不同。
- 某些 operation 可能要求 TiKHub 提供的 Cookie/登录态；Cookie 属于敏感凭据，不能写入 URL、日志或 Git。
- 文章内容可能被删除、改版或限制访问。采集任务应该记录 HTTP 状态、文档版本和最后成功时间。
- 若用于转载、搜索索引或商业分发，需要单独核对微信公众平台规则、文章版权和个人信息处理依据。

## 5. 微博（Weibo）

### 5.1 典型能力和查文档关键词

文档可能按 Weibo、Web、App 分组。常见路径形态是 /api/v1/weibo/{web|app}/{operation}；不要假设 Web 和 App 版本的字段完全相同。

| 目标 | 需要在文档中寻找的 operation 关键词 | 首选入参 |
| --- | --- | --- |
| 用户资料 | user info、profile、user detail | uid/user_id；有时支持 screen_name |
| 用户微博列表 | user posts、timeline、statuses | uid/user_id、page/count 或 since_id/max_id |
| 单条微博详情 | post detail、status detail、show | id/mid/status_id |
| 关键词搜索 | search、search result | keyword/q、页码和数量 |
| 评论/转发 | comments、reposts | 微博 id/mid、分页字段 |

用户资料请求模板：

~~~bash
export TIKHUB_PATH='/api/v1/weibo/<web-or-app>/<user-operation>'
export TIKHUB_QUERY='uid=<微博UID>'

curl --fail-with-body \
  -H "Authorization: Bearer $TIKHUB_API_KEY" \
  -H 'Accept: application/json' \
  "$TIKHUB_BASE_URL$TIKHUB_PATH?$TIKHUB_QUERY"
~~~

用户时间线模板：

~~~bash
export TIKHUB_PATH='/api/v1/weibo/<web-or-app>/<user-posts-operation>'
export TIKHUB_QUERY='uid=<微博UID>&page=1&count=20'

curl --fail-with-body \
  -H "Authorization: Bearer $TIKHUB_API_KEY" \
  -H 'Accept: application/json' \
  "$TIKHUB_BASE_URL$TIKHUB_PATH?$TIKHUB_QUERY"
~~~

### 5.2 ID 和增量同步

- 长期存储优先使用微博 uid 作为用户主键，使用微博 id 或文档明确说明的 mid 作为内容主键。
- screen_name 会变化且可能重名，不应作为唯一键。
- 时间线接口如果返回 since_id、max_id 或类似字段，应按文档方向做增量：保存最新一条 ID，下一轮只取更新内容，并处理置顶微博、删除微博和分页重叠。
- 搜索结果是动态窗口，分页翻页期间可能发生插入；需要去重，并记录抓取时间而不是把页码当作永久游标。

### 5.3 微博的实际风险

- Web/App operation 往往对会话、设备指纹、地区和频率敏感；看到 403 或频繁空结果时，不应简单加大并发。
- 转发、评论、长文和媒体字段可能是嵌套对象；保留原始 JSON，统一层只提取 id/text/created_at/author/media 等稳定子集。
- 如果文档提供“发帖、评论、点赞”等写操作，先按最小权限评估；本文只覆盖读取型接口，不把读取 Key 当成可写 Key。

## 6. X/Twitter

### 6.1 典型能力和查文档关键词

文档可能使用 Twitter、X、Twitter Web 或 Twitter App 标签。常见路径形态是 /api/v1/twitter/{web|app}/{operation}，但必须以当前文档为准。

| 目标 | 需要在文档中寻找的 operation 关键词 | 首选入参 |
| --- | --- | --- |
| 用户资料 | user lookup、profile、user info | username/screen_name 或 user_id |
| 用户发帖列表 | user tweets、timeline、statuses | user_id（优先）、cursor/pagination_token |
| 单条帖子详情 | tweet detail、tweet lookup、status | tweet_id/status_id |
| 关键词/高级搜索 | search tweets、search、recent | query/q、游标 |
| 用户关系 | followers、following、friends | user_id、游标 |

用户资料模板：

~~~bash
export TIKHUB_PATH='/api/v1/twitter/<web-or-app>/<user-operation>'
export TIKHUB_QUERY='username=<screen_name>'

curl --fail-with-body \
  -H "Authorization: Bearer $TIKHUB_API_KEY" \
  -H 'Accept: application/json' \
  "$TIKHUB_BASE_URL$TIKHUB_PATH?$TIKHUB_QUERY"
~~~

搜索模板：

~~~bash
export TIKHUB_PATH='/api/v1/twitter/<web-or-app>/<search-operation>'
export TIKHUB_QUERY='query=%28AI%20OR%20LLM%29%20lang%3Aen&count=20'

curl --fail-with-body \
  -H "Authorization: Bearer $TIKHUB_API_KEY" \
  -H 'Accept: application/json' \
  "$TIKHUB_BASE_URL$TIKHUB_PATH?$TIKHUB_QUERY"
~~~

### 6.2 用户名、ID 和游标

- username/screen_name 适合第一次查找；解析后应立即保存数字 user_id。
- 帖子详情优先使用 tweet_id。X 的 URL 中的数字 ID 比显示文本稳定。
- 搜索语法（例如 from:、since:、until:、lang:）是否由 TiKHub 原样透传，要以 operation 说明为准；不能因为上游 X 支持就假设 TiKHub 支持。
- 常见下一页字段有 next_token、next_cursor、cursor 或 pagination_token。把它当作不透明字符串回传，不要自行 URL 解码或转数字。
- 用户时间线与搜索的去重键建议统一为 tweet_id；媒体下载 URL 可能过期，正文和元数据应先落盘。

### 6.3 X/Twitter 的实际风险

- X 的官方 API、网页端接口和 App 接口权限模型不同。TiKHub 的“Twitter API”不等于 X 官方 API，套餐、字段和稳定性要单独核对。
- 可能需要 X 会话 Cookie、Guest token 或其他平台凭据。凭据失效时应更新会话或切换套餐，不要把它们硬编码进客户端。
- 搜索结果受时间窗口、语言和可见性影响；将 query、游标、抓取时间和时区写入原始记录，方便复现。
- 关注、发帖、点赞等写操作涉及账户风险和平台规则。除非文档明确说明授权范围与回滚方式，否则保持读取模式。

## 7. 统一数据模型建议

无论来源平台如何命名，可以在业务层归一化为：

~~~json
{
  "platform": "wechat_mp | weibo | x",
  "object_type": "account | post | article | comment",
  "platform_id": "原平台稳定 ID",
  "author_id": "作者/账号稳定 ID",
  "author_name": "显示名",
  "created_at": "原始时间字符串",
  "text": "纯文本（可为空）",
  "html": "原始 HTML（可为空）",
  "media": [],
  "source_url": "原平台 URL",
  "tikhub_path": "实际调用的 path",
  "request_params_redacted": {},
  "fetched_at": "UTC 时间",
  "raw_response_file": "原始响应文件路径"
}
~~~

建议同时保存：

- docs_fetched_at：读取 OpenAPI/文档的时间。
- docs_sha256：OpenAPI 文件哈希，便于发现字段变更。
- request_id：若响应头或响应体提供。
- next_cursor：下一页游标及其所属请求参数。
- 脱敏后的请求参数；Cookie、Bearer Key 和完整签名 URL 不进入日志。

## 8. 拿到 Key 后的验收清单

按下面顺序做一次小流量验收，能尽快发现“路径/权限/上游会话”是哪一层出问题：

1. 拉取 openapi.json，记录真实 servers、认证 scheme 和三个平台的 paths。
2. 每个平台只选一个最小读取 operation，先用一个明确存在的公开 ID 请求一次。
3. 核对 HTTP 状态、业务 code、响应中的分页字段和限流头。
4. 用一个无效 ID 验证 404/业务错误的格式；用缺少必填参数的请求验证 422/参数错误格式。
5. 分别做两页分页，确认下一页不会重复或漏掉主键。
6. 记录单次请求耗时、响应大小和套餐消耗；再决定并发度和缓存时间。
7. 将实际 path、参数名和成功响应示例回填本文，把所有 <...> 占位符替换掉。

## 9. 参考入口

- TiKHub 首页：https://tikhub.io/
- TiKHub 文档候选入口：https://docs.tikhub.io/
- TiKHub Swagger/ReDoc/OpenAPI 候选入口：https://api.tikhub.io/docs、https://api.tikhub.io/redoc、https://api.tikhub.io/openapi.json
- X 官方 API 文档（用于区分官方权限模型）：https://developer.x.com/en/docs
- 微博开放平台文档（用于区分官方 REST 权限模型）：https://open.weibo.com/wiki/RESTful_API
- 微信公众平台开发文档（用于区分官方公众号能力）：https://developers.weixin.qq.com/doc/offiaccount/Getting_Started/Overview.html

> 以上三组官方链接只用于对比权限和数据模型；TiKHub 的真实可用字段、套餐、速率限制和 operation 路径，以 TiKHub 当前账户文档为准。
