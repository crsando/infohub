import json

from xnews.config import Config
from xnews.export import export_posts
from xnews.parser import NormalizedPost
from xnews.store import Store


def test_export_contains_frontmatter_and_entities(tmp_path):
    store = Store(tmp_path / "xnews.db")
    try:
        store.upsert_account("macro", "MacroMargin", "MacroMargin", "42", added_at="now")
        post = NormalizedPost(
            post_id="123",
            author_rest_id="42",
            author_screen_name="MacroMargin",
            author_name="Macro",
            text="A post",
            created_at=100,
            url="https://x.com/MacroMargin/status/123",
            is_pinned=False,
            is_reply=False,
            is_repost=False,
            entities={
                "urls": [{"expanded_url": "https://example.test/source"}],
                "media": [{"media_url_https": "https://example.test/image.jpg"}],
            },
            raw_object={},
            content_hash="hash",
        )
        with store.transaction():
            store.save_post(post, watch_key="macro", collected_at=200, raw_path=None)
        cfg = Config()
        cfg.export.enabled = True
        cfg.export.dir = str(tmp_path / "markdown")
        written = export_posts(store, cfg)
        assert len(written) == 1
        text = written[0].read_text(encoding="utf-8")
        assert "帖子ID" in text and "A post" in text
        assert "https://example.test/source" in text
        assert "https://example.test/image.jpg" in text
        assert store.get_post("123")["markdown_path"]
    finally:
        store.close()
