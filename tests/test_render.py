from infohub.memos import markdown_title, render_memo


def test_render_uses_compact_linked_title_and_summary_body():
    body = render_memo(
        {
            "item_key": "xnews:123",
            "source": "xnews",
            "author": "Macro",
            "title": "一条帖子",
            "published_at": 1,
            "source_url": "https://x.com/a/status/123",
        },
        "- 核心观点",
        ["infohub"],
    )
    assert body.startswith("[一条帖子](https://x.com/a/status/123)")
    assert "来源：X/Twitter · Macro · " in body
    assert "## 摘要" not in body
    assert "原文：" not in body
    assert "#xnews" in body
    assert "<!--" not in body


def test_markdown_title_falls_back_to_plain_text_without_url():
    assert markdown_title("没有链接", "") == "没有链接"
    assert markdown_title("标题 ]", "https://example.test/a_(1)") == "[标题 \\]](https://example.test/a_(1\\))"
