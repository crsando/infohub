from pathlib import Path

from infohub.store import Store


def item(body="正文"):
    return {
        "item_key": "wxmp:https://example/article",
        "source": "wxmp",
        "source_id": "https://example/article",
        "author": "测试号",
        "title": "标题",
        "body": body,
        "source_url": "https://example/article",
        "published_at": 10,
        "collected_at": 20,
        "content_hash": body,
    }


def test_upsert_is_idempotent_and_tracks_content_change(tmp_path: Path):
    with Store(tmp_path / "timeline.db") as store:
        assert store.upsert_item(item("a")) == (True, True)
        assert store.upsert_item(item("a")) == (False, False)
        assert store.upsert_item(item("b")) == (False, True)
        assert store.stats()["items"] == 1


def test_summary_versions_and_pending_queue(tmp_path: Path):
    with Store(tmp_path / "timeline.db") as store:
        store.upsert_item(item("hash"))
        first = store.ensure_summary("wxmp:https://example/article", "hash", "v1", "model")
        assert store.pending_summaries()[0]["id"] == first
        store.mark_summary_running(first)
        store.save_summary_result(first, summary="摘要", status="ok")
        assert store.stats()["summaries_ok"] == 1
        changed = item("new body")
        changed["content_hash"] = "new-hash"
        store.upsert_item(changed)
        second = store.ensure_summary("wxmp:https://example/article", "new-hash", "v1", "model")
        assert second != first
        assert store.stats()["summaries_pending"] == 1
