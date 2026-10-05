# TiKHub X/Twitter 接口与 Token 使用说明

> 研究对象：TiKHub 的 X/Twitter Web 接口
>
> API Host：`https://api.tikhub.io`
>
> 本文记录了当前 OpenAPI 中已验证的读取接口和调用方式。接口字段、可用性、计费和上游平台限制可能变化，实际使用前应以 TiKHub 控制台的 OpenAPI 为准。

## 1. Token 配置

TiKHub 请求使用 Bearer Token：

```http
Authorization: Bearer <TIKHUB_TOKEN>
```

推荐只在当前 shell 或运行环境中设置环境变量：

```bash
export TIKHUB_TOKEN='从 TiKHub 控制台获取的 token'
```

检查是否配置时不要打印 token 本身：

```bash
test -n "$TIKHUB_TOKEN" && echo 'TIKHUB_TOKEN is set' || echo 'TIKHUB_TOKEN is missing'
```

安全要求：

- 不要把 token 写入 Git、配置样例、命令输出、HTTP 日志或异常堆栈。
- 不要把 token 放在 URL 查询参数中；它只放在 `Authorization` 请求头。
- 共享日志或报告时只保留 HTTP 状态、TiKHub 业务码和脱敏后的错误摘要。
- 如果 token 曾经出现在公开日志或仓库中，应立即在 TiKHub 控制台撤销并重新生成。

下面的示例都假设已经设置了 `TIKHUB_TOKEN` 和 `TIKHUB_BASE_URL`：

```bash
export TIKHUB_BASE_URL='https://api.tikhub.io'
```

## 2. 通用请求模板

### cURL

```bash
curl --fail-with-body --connect-timeout 10 --max-time 60 \
  -H "Authorization: Bearer $TIKHUB_TOKEN" \
  -H 'Accept: application/json' \
  "$TIKHUB_BASE_URL/api/v1/twitter/web/<operation>?<query>"
```

### Python

```python
import os
import requests


BASE_URL = os.getenv("TIKHUB_BASE_URL", "https://api.tikhub.io")
TOKEN = os.environ["TIKHUB_TOKEN"]


def tikhub_get(path: str, params: dict[str, str]) -> dict:
    response = requests.get(
        f"{BASE_URL.rstrip('/')}/{path.lstrip('/')}",
        params=params,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json",
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    # TiKHub 的包装格式可能同时包含 HTTP 状态和业务 code。
    if isinstance(payload, dict) and payload.get("code") not in (None, 0, 200, "0", "200"):
        raise RuntimeError(payload.get("message") or payload.get("msg") or payload)
    return payload
```

`code` 的正式成功值应以当前 OpenAPI/控制台为准。业务错误不能只看 HTTP 200，需要同时检查响应中的 `code`、`message` 或 `msg`。

## 3. 已验证的接口

### 3.1 搜索账号或帖子

```http
GET /api/v1/twitter/web/fetch_search_timeline
```

常用查询参数：

| 参数 | 说明 |
| --- | --- |
| `keyword` | 搜索关键词；搜索账号时填写名称或用户名 |
| `search_type` | 搜索类型。账号搜索使用 `People` |
| `cursor` | 翻页游标；首次请求可省略 |

搜索账号示例：

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $TIKHUB_TOKEN" \
  -H 'Accept: application/json' \
  --get "$TIKHUB_BASE_URL/api/v1/twitter/web/fetch_search_timeline" \
  --data-urlencode 'keyword=Herman Jin' \
  --data-urlencode 'search_type=People'
```

账号结果中重点保存：

```text
data.*.user_info.name
data.*.user_info.screen_name
data.*.user_info.rest_id
```

实际返回包装层可能不同，先用 `--raw` 或保存 JSON 后确认 `data` 下的数组位置。搜索得到的 `screen_name` 和 `rest_id` 应再调用用户资料接口核验，不要仅以显示名称作为唯一标识。

### 3.2 获取用户资料

```http
GET /api/v1/twitter/web/fetch_user_profile
```

支持的定位参数：

| 参数 | 说明 |
| --- | --- |
| `screen_name` | X 用户名，不含 `@`；例如 `ShanghaoJin` |
| `rest_id` | X 的数字用户 ID；已知时优先使用 |

示例：

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $TIKHUB_TOKEN" \
  -H 'Accept: application/json' \
  --get "$TIKHUB_BASE_URL/api/v1/twitter/web/fetch_user_profile" \
  --data-urlencode 'screen_name=ShanghaoJin'
```

建议把返回的 `rest_id`、`screen_name`、显示名称、头像和简介一起保存；用户名可能改变，数字 ID 更适合作为长期主键。

### 3.3 获取用户最新帖子

```http
GET /api/v1/twitter/web/fetch_user_post_tweet
```

参数：

| 参数 | 说明 |
| --- | --- |
| `screen_name` | 用户名；与 `rest_id` 二选一 |
| `rest_id` | 数字用户 ID；与 `screen_name` 二选一 |
| `cursor` | 下一页游标；首次请求省略 |

按用户名读取：

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $TIKHUB_TOKEN" \
  -H 'Accept: application/json' \
  --get "$TIKHUB_BASE_URL/api/v1/twitter/web/fetch_user_post_tweet" \
  --data-urlencode 'screen_name=MacroMargin'
```

按数字 ID 读取：

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $TIKHUB_TOKEN" \
  -H 'Accept: application/json' \
  --get "$TIKHUB_BASE_URL/api/v1/twitter/web/fetch_user_post_tweet" \
  --data-urlencode 'rest_id=858124064476479488'
```

已验证响应中常见的字段为：

```text
data.timeline       # 用户时间线帖子
data.pinned         # 置顶帖子，可能与时间线分开返回
data.next_cursor    # 下一页游标
```

如果目标是“最新 N 条”，应从 `data.timeline` 读取并去重，再按 `created_at` 或接口返回顺序取前 N 条。`data.pinned` 是置顶内容，不应在没有明确需求时混入最新帖子列表。保存 `next_cursor` 后再请求下一页：

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $TIKHUB_TOKEN" \
  -H 'Accept: application/json' \
  --get "$TIKHUB_BASE_URL/api/v1/twitter/web/fetch_user_post_tweet" \
  --data-urlencode 'screen_name=MacroMargin' \
  --data-urlencode 'cursor=<上一页返回的 data.next_cursor>'
```

帖子至少应保存帖子 ID、文本、创建时间、作者 ID 和原始 JSON。帖子 URL 通常可以按 `https://x.com/<screen_name>/status/<tweet_id>` 生成，但如果响应已经提供 URL，应优先使用响应值。

### 3.4 获取用户关注列表

```http
GET /api/v1/twitter/web/fetch_user_followings
```

参数：

| 参数 | 说明 |
| --- | --- |
| `screen_name` | 用户名 |
| `cursor` | 下一页游标；首次请求省略 |

示例：

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $TIKHUB_TOKEN" \
  -H 'Accept: application/json' \
  --get "$TIKHUB_BASE_URL/api/v1/twitter/web/fetch_user_followings" \
  --data-urlencode 'screen_name=Crsando93'
```

已验证响应中常见的字段为：

```text
data.following      # 当前页关注的账号
data.next_cursor    # 下一页游标
data.more_users     # 是否还有更多账号（如果接口返回）
```

接口返回的是当前排序下的关注列表，并没有关注发生时间字段。因此只能列出接口当前顺序的前 N 个，不能据此严格判断“最近关注的 N 个人”。若要同步完整列表，应循环请求 `next_cursor`，同时按 `rest_id` 去重并保存抓取时间。

## 4. 推荐的账号读取流程

1. 用 `fetch_search_timeline` 的 `search_type=People` 搜索名称或用户名。
2. 从搜索结果提取 `name`、`screen_name` 和 `rest_id`。
3. 用 `fetch_user_profile` 核验账号，记录稳定的 `rest_id`。
4. 用 `fetch_user_post_tweet` 拉取时间线，保存 `data.next_cursor` 做分页。
5. 去除重复帖子；根据需求决定是否单独保存 `data.pinned`。
6. 需要关系数据时调用 `fetch_user_followings`，明确记录这是当前列表顺序，不是按关注时间排序的结果。

## 5. 已实测账号样例

### Herman Jin

- 搜索到的 `screen_name`：`ShanghaoJin`
- `rest_id`：`858124064476479488`
- `fetch_user_profile` 调用成功。
- `fetch_user_post_tweet` 调用成功并返回时间线和下一页游标。

### Crsando93

- `fetch_user_followings` 调用成功。
- 返回了关注列表、分页游标及 `more_users` 等字段。
- 返回对象没有关注时间字段，所以无法从该接口严格推断最近关注顺序。

## 6. 错误与限流处理

- `401/403`：检查 token、套餐、接口权限或 TiKHub 上游会话配置；修正前不要重复重试。
- `404/422`：检查 operation 路径、参数名和参数类型。
- `429`：读取 `Retry-After`（如果返回），采用有限次数指数退避，避免并发放大请求。
- `5xx`、连接超时：只做有限次数重试，并记录 HTTP 状态、请求路径和脱敏后的响应摘要。
- HTTP 成功但 TiKHub `code` 表示失败时，按业务错误处理，不要把空 `data` 当作正常结果。
- 某些接口或数据量可能产生计费，批量翻页前先确认 TiKHub 控制台的套餐、余额和调用单价。

生产采集建议保存原始响应、请求时间、接口路径、查询参数（排除 token）和分页游标。上游 X 页面或接口改版时，原始响应有助于重新解析。

## 7. 本仓库 demo 的兼容性说明

`demo/tikhub-x-latest/` 是早期的可配置脚本，当前默认值仍是：

```text
环境变量：TIKHUB_API_KEY
接口：/api/v1/twitter/web/get_user_tweets
```

它支持通过命令行覆盖 endpoint 和参数名，但没有把本文验证的 `fetch_user_post_tweet` 固化为默认值。使用该 demo 时应按其 README 显式传入旧接口所需配置，或直接按本文的 `TIKHUB_TOKEN + fetch_user_post_tweet` 示例调用。不要把两个环境变量或两套接口名称混用。
