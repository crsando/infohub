# infohub

本目录存放 TiKHub 相关的采集工具、实验代码和研究资料。

| 路径 | 用途 | 状态 |
| --- | --- | --- |
| [wxmp/](wxmp/) | 微信公众号订阅、全文采集、SQLite 检索和 Markdown 导出 | 可用；31 项测试通过 |
| [demo/tikhub-x-latest/](demo/tikhub-x-latest/) | 读取 X/Twitter 账号最新帖子的 uv demo | 旧版可配置脚本；当前接口用法见 X 文档 |
| [tikhub-api-notes.md](tikhub-api-notes.md) | TiKHub 通用调用与三平台研究笔记 | 含尚未实时核实的路径示例 |
| [tikhub-x-api.md](tikhub-x-api.md) | 已验证的 TiKHub X/Twitter 接口与 token 使用说明 | 包含账号搜索、资料、帖子和关注列表 |
| [docs/LLM-SUMMARY.md](docs/LLM-SUMMARY.md) | 本地 OpenAI-compatible Qwen 摘要接口调用方式 | 已验证 `enable_thinking=false` 请求和限制 |
| [docs/INFOHUB-DESIGN.md](docs/INFOHUB-DESIGN.md) | `infohub` 统一采集、摘要和 Memos 发布设计 | 设计与当前 MVP 实现说明 |
| [docs/MEMOS-TEMPLATE-DESIGN.md](docs/MEMOS-TEMPLATE-DESIGN.md) | Memos 简洁 Markdown 内容模板 | 已实现 |
| [src/infohub/](src/infohub/) | 统一采集、Qwen 摘要和 Memos 发布 CLI | MVP 已实现；使用前运行 `infohub init` |

## 开始使用

微信公众号工具：

```bash
cd wxmp
uv sync --locked
uv run wxmp --help
uv run --locked --group dev pytest -q
```

X/Twitter demo：

```bash
cd demo/tikhub-x-latest
export TIKHUB_API_KEY='在本地 shell 设置你的 token'
uv run main.py --account MacroMargin
```

上面的 demo 仍使用旧版 `TIKHUB_API_KEY` 和可配置 endpoint。当前已验证的 X/Twitter 接口、`TIKHUB_TOKEN` 配置方式和 cURL/Python 示例见 [tikhub-x-api.md](tikhub-x-api.md)。

运行时配置、API token、SQLite 数据库和原始响应属于本地数据，不应提交到仓库。`wxmp` 的配置文件默认位于 `~/.config/wxmp/config.json`，初始配置和权限要求见 [wxmp/README.md](wxmp/README.md)。

统一入口 `infohub` 的设计见 [docs/INFOHUB-DESIGN.md](docs/INFOHUB-DESIGN.md)。初始化并运行：

```bash
uv sync --locked
uv run infohub init
export TIKHUB_TOKEN='你的 TiKHub token'
export MEMOS_TOKEN='你的 Memos token'
export MEMOS_URL='http://127.0.0.1:5230'
uv run infohub check --llm --memos
uv run infohub run
```

也可以分阶段执行 `uv run infohub ingest`、`uv run infohub summarize` 和 `uv run infohub publish`。Qwen 接口地址和模型位于 `~/.config/infohub/config.json`，可用 `LLM_BASE_URL` 临时覆盖。
