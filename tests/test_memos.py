import json

import pytest

from infohub.config import MemosConfig
from infohub.errors import MemosError
from infohub.memos import MemosClient, remote_ids


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return Response({"memos": [{"name": "memos/1", "content": "existing memo"}]})

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return Response({"name": "memos/2", "uid": "uid-2"})


def test_memos_client_normalizes_url_and_auth():
    session = Session()
    client = MemosClient(MemosConfig(base_url="http://memos.local/"), "secret", session)
    assert client.check()["memos"][0]["name"] == "memos/1"
    created = client.create("正文")
    assert remote_ids(created) == ("memos/2", "uid-2")
    method, url, kwargs = session.calls[-1]
    assert method == "POST"
    assert url == "http://memos.local/api/v1/memos"
    assert kwargs["headers"]["Authorization"] == "Bearer secret"
    assert json.loads(json.dumps(kwargs["json"], ensure_ascii=False))["content"] == "正文"


def test_memos_requires_token():
    with pytest.raises(MemosError):
        MemosClient(MemosConfig(), "")
