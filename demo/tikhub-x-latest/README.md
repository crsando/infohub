# TiKHub X/Twitter 最新帖子 Demo

这个 demo 使用 TiKHub 读取 X/Twitter 账号的最新帖子，默认账号为 MacroMargin。API token 不写入文件，运行时从 TIKHUB_API_KEY 环境变量读取。

## 用 uv 执行

在本目录执行：

~~~bash
export TIKHUB_API_KEY='在当前 shell 中设置你的 TiKHub token'
uv run main.py --account MacroMargin
~~~

默认请求：

~~~text
GET https://api.tikhub.io/api/v1/twitter/web/get_user_tweets
  ?username=MacroMargin&count=10
Authorization: Bearer $TIKHUB_API_KEY
~~~

TiKHub 文档如果使用不同的 operation 或参数名，可以覆盖默认值：

~~~bash
uv run main.py \
  --account MacroMargin \
  --endpoint /api/v1/twitter/web/<当前文档中的 operation> \
  --user-param user_id \
  --limit-param count \
  --limit 10
~~~

也可以用环境变量覆盖：

~~~bash
export TIKHUB_X_ENDPOINT='/api/v1/twitter/web/<operation>'
export TIKHUB_X_USER_PARAM='username'
uv run main.py
~~~

## 调试和原始响应

只看原始 JSON：

~~~bash
uv run main.py --raw
~~~

同时保存原始响应：

~~~bash
uv run main.py --save-raw ./artifacts/macro-margin.json
~~~

增加文档要求的额外 query 参数：

~~~bash
uv run main.py --extra lang=en --extra include_replies=false
~~~

脚本只输出帖子摘要和 URL，不输出 Authorization header。错误信息会对 TIKHUB_API_KEY 做脱敏。

## 说明

默认 path 是 TiKHub Twitter Web 接口常见的命名形态，是否与当前账户文档一致需要以 TiKHub OpenAPI 为准。若接口要求先用 username 换取 user_id，再按 user_id 拉时间线，可以先在文档中找到用户查询 operation，再将结果作为：

~~~bash
uv run main.py --user-param user_id --account '<解析出来的 user_id>'
~~~

本目录不包含任何真实 token。
