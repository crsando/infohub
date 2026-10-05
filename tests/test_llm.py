from infohub.config import LLMConfig
from infohub.llm import LLMClient, build_payload


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{"message": {"content": "- 这是摘要"}}],
            "usage": {"completion_tokens": 10},
        }


class FakeSession:
    def post(self, *args, **kwargs):
        self.payload = kwargs["json"]
        return FakeResponse()


def test_payload_has_top_level_thinking_option():
    payload = build_payload({"source": "xnews", "author": "A", "body": "正文"}, LLMConfig())
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert payload["messages"][0]["role"] == "system"


def test_payload_uses_prompts_from_config():
    config = LLMConfig(
        system_prompt="系统提示",
        user_prompt_template="标题={title}\n正文={body}",
    )
    payload = build_payload(
        {"source": "wxmp", "title": "配置标题", "body": "配置正文"}, config
    )
    assert payload["messages"][0]["content"] == "系统提示"
    assert payload["messages"][1]["content"] == "标题=配置标题\n正文=配置正文"


def test_client_extracts_content_without_reasoning():
    session = FakeSession()
    result = LLMClient(LLMConfig(retries=0), session=session).summarize(
        {"source": "wxmp", "author": "A", "title": "标题", "body": "正文"}
    )
    assert result.summary == "- 这是摘要"
    assert session.payload["chat_template_kwargs"]["enable_thinking"] is False
