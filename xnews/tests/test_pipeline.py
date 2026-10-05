import json
from pathlib import Path

from xnews.config import Account, Config
from xnews.pipeline import run


def test_two_runs_are_idempotent(monkeypatch, tmp_path):
    responses = [
        {
            "code": 200,
            "data": {
                "timeline": [
                    {
                        "rest_id": "2",
                        "text": "newer",
                        "created_at": "2025-10-15T00:00:00Z",
                        "user_info": {"rest_id": "42", "screen_name": "MacroMargin", "name": "Macro"},
                    },
                    {
                        "rest_id": "1",
                        "text": "older",
                        "created_at": "2025-10-14T00:00:00Z",
                        "user_info": {"rest_id": "42", "screen_name": "MacroMargin", "name": "Macro"},
                    },
                ],
                "next_cursor": "ignored-after-known",
            },
        }
    ]

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.calls = 0

        def user_posts(self, **kwargs):
            self.calls += 1
            return responses[0]

        def close(self):
            pass

    monkeypatch.setattr("xnews.pipeline.TikHubClient", FakeClient)
    cfg = Config(
        storage=__import__("xnews.config", fromlist=["StorageConfig"]).StorageConfig(data_dir=str(tmp_path / "data")),
        export=__import__("xnews.config", fromlist=["ExportConfig"]).ExportConfig(enabled=True, dir=str(tmp_path / "md")),
        accounts=[Account(key="macro", nick="MacroMargin", screen_name="MacroMargin", rest_id="42")],
    )
    cfg.provider.token = "test-token"
    with __import__("xnews.store", fromlist=["Store"]).Store(tmp_path / "data" / "xnews.db") as store:
        first = run(cfg, store)
        second = run(cfg, store)
        assert first.total_new == 2
        assert second.total_new == 0
        assert store.stats()["posts"] == 2
        assert len(list((tmp_path / "data" / "raw" / "responses").rglob("*.json"))) == 2
        assert len(list((tmp_path / "md").rglob("*.md"))) == 2
