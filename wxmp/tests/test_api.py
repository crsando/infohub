"""上游账号搜索响应的解析回归测试。"""

from wxmp.api import TikHubClient
from wxmp.resolve import to_candidates


def test_search_accounts_reads_official_username_and_skips_channels(monkeypatch):
    items = [
        {
            "title": '<em class="highlight">李佳图</em>',
            "accTypeName": "公众号",
            "jumpInfo": {
                "aliasName": "",
                "userName": "gh_802cdf8fbf29",
            },
            "source": {"title": "个人"},
        },
        {
            "title": "有别名的号",
            "accTypeName": "公众号",
            "jumpInfo": {
                "aliasName": "custom_alias",
                "userName": "gh_another",
            },
        },
        {
            "title": "李佳图视频",
            "accTypeName": "视频号",
            "jumpInfo": {"userName": "channel@finder"},
        },
    ]
    client = TikHubClient("test-token")
    monkeypatch.setattr(client, "_post", lambda *_: {"data": {"items": items}})

    try:
        results = client.search_accounts("李佳图")
    finally:
        client.close()

    assert [(item["nick"], item["username"], item["user_name"]) for item in results] == [
        ("李佳图", "gh_802cdf8fbf29", "gh_802cdf8fbf29"),
        ("有别名的号", "custom_alias", "gh_another"),
    ]
    assert len(to_candidates(results)) == 2
