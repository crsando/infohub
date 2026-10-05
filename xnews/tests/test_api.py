from xnews.api import EP_USER_POSTS, TikHubClient
from xnews.errors import AuthError


class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"code": 200, "data": {"timeline": []}}


def test_user_posts_request_contract(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params, timeout))
        return FakeResponse()

    client = TikHubClient("secret-token", base_url="https://example.test", qps=0, max_retries=0)
    monkeypatch.setattr(client.session, "get", fake_get)
    try:
        payload = client.user_posts(rest_id="123", cursor="next")
    finally:
        client.close()
    assert payload["code"] == 200
    assert calls == [
        (
            "https://example.test" + EP_USER_POSTS,
            {"rest_id": "123", "cursor": "next"},
            60,
        )
    ]
    assert client.session.headers["Authorization"] == "Bearer secret-token"


def test_unauthorized_is_auth_error(monkeypatch):
    class Response(FakeResponse):
        status_code = 401
        text = "bad"

    client = TikHubClient("secret-token", qps=0, max_retries=0)
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: Response())
    try:
        try:
            client.user_posts(screen_name="someone")
        except AuthError:
            pass
        else:
            raise AssertionError("expected AuthError")
    finally:
        client.close()
