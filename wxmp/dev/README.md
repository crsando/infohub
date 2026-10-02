# dev/ —— 开发参考资料

运行时**不会**读取这个目录里的任何东西。它是给改代码、换上游时查阅用的。

## openapi-wechat.json

TikHub 官方 OpenAPI 规格（`https://api.tikhub.io/openapi.json`，原文件约 3 MB、1050 个接口）的裁剪版，
只保留 `wechat_mp/*` 与 `wechat_search/*` 共 13 个接口及其引用的 schema。2026-10-02 裁剪。

上游接口变了先重新拉官方规格对比这份。

## api-samples/

2026-10-02 用真实 token 调用拿到的原始响应，样本账号「仓都加满」（`mtlsnow`）。
**不含 token**（已检查）。

| 文件 | 接口 | wxmp 用在哪 |
|---|---|---|
| `wechat_search.fetch_search.account.json` | `POST /api/v1/wechat_search/v2/fetch_search`（`business_type=account`） | `api.search_accounts` → `wxmp add <名称>` |
| `wechat_mp.fetch_account_profile.json` | `POST /api/v1/wechat_mp/v2/fetch_account_profile` | `api.account_profile` → `wxmp add <标识>` 核实账号 |
| `wechat_mp.fetch_account_articles.json` | `POST /api/v1/wechat_mp/v2/fetch_account_articles` | `api.account_articles` → `wxmp run` 列表 |
| `wechat_mp.fetch_article_detail_h5.content.json` | `POST /api/v1/wechat_mp/v2/fetch_article_detail_h5` | `api.article_detail` → 正文。**注意：只存了 `data.content` 这一层**，不是完整信封 |
| `wechat_mp.fetch_article_stats_h5.json` | `POST /api/v1/wechat_mp/v2/fetch_article_stats_h5` | **wxmp 不使用**。留作证据：阅读/点赞等字段全为 `null` |

这些样本也适合将来写离线测试（mock 上游）时当 fixture 用。

## 跑测试

```sh
uv sync --group dev
uv run pytest -q
```
