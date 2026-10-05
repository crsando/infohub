# Qwen 摘要接口调用说明

本文记录已在本机验证的 OpenAI-compatible LLM 接口调用方式，供后续 `infohub` 编排层复用。

## 1. 服务信息

| 项目 | 值 |
| --- | --- |
| Base URL | `http://192.168.5.17:8000/v1` |
| 模型 | `incoai/Qwen3.8-27B-Splash` |
| 接口 | `POST /chat/completions` |
| 协议 | OpenAI Chat Completions 兼容协议 |
| 已验证耗时 | 约 9.9 秒（单篇短公众号正文） |

模型列表可用以下命令检查：

```bash
curl --fail --max-time 10 http://192.168.5.17:8000/v1/models
```

不要把任何访问令牌写入本文档。当前接口在内网可直接访问时可以不带认证；如果部署启用了认证，令牌应从环境变量读取并只放入请求头。

## 2. 推荐请求体

提示词不再写死在调用代码中，而是从 `~/.config/infohub/config.json` 的 `llm` 字段读取：

```json
{
  "prompt_version": "summary-v1",
  "system_prompt": "你是中文资讯编辑。请严格依据原文，提炼 2-4 条简短要点。只陈述原文明确表达的事实、作者判断或预测，不补充常识，不猜测缺失信息。保留重要主体、数字、时间和条件；不要给出投资建议或风险评级。",
  "user_prompt_template": "平台：{source}\n作者：{author}\n标题：{title}\n发布时间：{published_at}\n原文链接：{source_url}\n正文：\n{body}\n\n请输出简短中文摘要，只保留 2-4 条要点。"
}
```

`user_prompt_template` 支持 `{source}`、`{author}`、`{title}`、`{published_at}`、`{source_url}` 和 `{body}`。修改提示词时递增 `prompt_version`，已有内容才会生成新的摘要版本。

```json
{
  "model": "incoai/Qwen3.8-27B-Splash",
  "messages": [
    {
      "role": "system",
      "content": "由 llm.system_prompt 配置项提供"
    },
    {
      "role": "user",
      "content": "由 llm.user_prompt_template 渲染提供"
    }
  ],
  "temperature": 0.1,
  "max_tokens": 700,
  "chat_template_kwargs": {
    "enable_thinking": false
  }
}
```

`chat_template_kwargs` 必须作为请求顶层字段传递，而不是放进 `messages`：

```json
{
  "chat_template_kwargs": {
    "enable_thinking": false
  }
}
```

## 3. Python 调用模板

```python
import os
import requests


BASE_URL = os.getenv("LLM_BASE_URL", "http://192.168.5.17:8000/v1").rstrip("/")
MODEL = os.getenv("LLM_MODEL", "incoai/Qwen3.8-27B-Splash")

system_prompt = "由 ~/.config/infohub/config.json 的 llm.system_prompt 提供"
user_prompt = "由 llm.user_prompt_template 使用 source、author、title、published_at、source_url、body 渲染提供"

payload = {
    "model": MODEL,
    "messages": [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ],
    "temperature": 0.1,
    "max_tokens": 700,
    "chat_template_kwargs": {"enable_thinking": False},
}

headers = {"Content-Type": "application/json"}
api_key = os.getenv("LLM_API_KEY", "").strip()
if api_key:
    headers["Authorization"] = f"Bearer {api_key}"

response = requests.post(
    f"{BASE_URL}/chat/completions",
    json=payload,
    headers=headers,
    timeout=180,
)
response.raise_for_status()
result = response.json()
message = result["choices"][0]["message"]
summary = (message.get("content") or "").strip()
```

生产代码必须同时检查：

1. HTTP 状态码；
2. `choices` 是否存在；
3. `message.content` 是否为空；
4. `usage` 和 `reasoning_content` 是否出现异常消耗。

## 4. 实测结果与限制

用本地已采集文章《人民币资产：价值重估的新阶段》测试，HTTP 状态为 `200`，返回了正常摘要。调用工具记录耗时约 `9.8867` 秒。

服务端同时返回了 `reasoning_content`，本次 `reasoning_tokens=549`。因此，虽然请求中传入了 `chat_template_kwargs.enable_thinking=false`，当前兼容层没有完全关闭 thinking。这个字段不能直接写入 Memos，只保存最终的 `message.content`。

长文章测试中曾出现模型把输出预算消耗在 reasoning、导致 `message.content` 为空的情况。`infohub` 设计应当：

- 对长正文设置输入长度和输出预算；
- 空内容时记录完整错误状态并有限重试；
- 重试仍失败时保留待处理状态，不能把空摘要发布到 Memos；
- 记录模型、提示词版本、耗时和 usage，便于定位服务端行为。

## 5. 摘要提示词约束

摘要只使用来源正文，不把模型的常识扩写成事实。公众号和 X 帖子统一遵循以下规则：

- 输出 2-4 条简短中文要点；
- 保留原文中的主体、数字、时间和条件；
- 明确区分事实、作者判断和预测；
- 不添加投资建议、风险评级或原文没有的结论；
- 没有足够正文时返回“正文不足，无法可靠摘要”，由调用方决定是否发布。
