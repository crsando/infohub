from xnews.parser import parse_page


def test_parse_timeline_pinned_and_nested_user():
    payload = {
        "code": 200,
        "data": {
            "timeline": [
                {
                    "rest_id": "100",
                    "full_text": "hello X",
                    "created_at": "Wed Oct 15 04:50:00 +0000 2025",
                    "user_info": {
                        "rest_id": "42",
                        "screen_name": "MacroMargin",
                        "name": "Macro Margin",
                    },
                    "entities": {"urls": [{"expanded_url": "https://example.test"}]},
                },
                {
                    "tweet": {
                        "tweet_id": "101",
                        "text": "nested",
                        "author": {"screen_name": "MacroMargin", "name": "Macro"},
                    }
                },
            ],
            "pinned": [
                {
                    "id_str": "100",
                    "text": "duplicate",
                    "user_info": {"screen_name": "MacroMargin"},
                }
            ],
            "next_cursor": "cursor-2",
        },
    }
    page = parse_page(payload)
    assert [p.post_id for p in page.timeline] == ["100", "101"]
    assert page.timeline[0].author_screen_name == "MacroMargin"
    assert page.timeline[0].created_at is not None
    assert page.pinned[0].post_id == "100"
    assert page.next_cursor == "cursor-2"
