from xnews.parser import NormalizedPost
from xnews.store import Store


def post(post_id="1", text="hello", author="MacroMargin"):
    return NormalizedPost(
        post_id=post_id,
        author_rest_id="42",
        author_screen_name=author,
        author_name="Macro",
        text=text,
        created_at=100,
        url=f"https://x.com/{author}/status/{post_id}",
        is_pinned=False,
        is_reply=False,
        is_repost=False,
        entities={},
        raw_object={"id": post_id, "text": text},
        content_hash=f"hash-{post_id}-{text}",
    )


def test_cross_account_dedup_and_update(tmp_path):
    store = Store(tmp_path / "xnews.db")
    try:
        store.upsert_account("a", "A", "MacroMargin", "42", added_at="now")
        store.upsert_account("b", "B", "Other", "43", added_at="now")
        with store.transaction():
            assert store.save_post(post(), watch_key="a", collected_at=200, raw_path=None) == "new"
        with store.transaction():
            assert store.save_post(post(), watch_key="b", collected_at=201, raw_path=None) == "known"
        assert store.conn.execute("SELECT count(*) FROM posts").fetchone()[0] == 1
        assert len(store.source_keys("1")) == 2
        changed = post(text="changed")
        with store.transaction():
            assert store.save_post(changed, watch_key="a", collected_at=202, raw_path="raw.json") == "updated"
        assert store.get_post("1")["text"] == "changed"
        assert len(store.search("changed")) == 1
    finally:
        store.close()


def test_short_search_falls_back_to_like(tmp_path):
    store = Store(tmp_path / "xnews.db")
    try:
        store.upsert_account("a", "A", "MacroMargin", "42", added_at="now")
        with store.transaction():
            store.save_post(post(text="AI news"), watch_key="a", collected_at=200, raw_path=None)
        assert len(store.search("AI")) == 1
    finally:
        store.close()
