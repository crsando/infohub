# xnews

X/Twitter 账号监听与本地归档工具。

项目通过配置文件监听 X/Twitter 账号，使用 TiKHub 获取帖子，按稳定帖子 ID 去重，
并保存 raw JSON 与 Markdown。完整设计和数据契约见 [docs/DESIGN.md](docs/DESIGN.md)。

## 快速开始

```sh
uv sync --group dev
uv run xnews init
export TIKHUB_TOKEN='你的 TiKHub token'
uv run xnews add MacroMargin --no-fetch
uv run xnews run
uv run xnews search 关键词
```

配置文件默认在 `~/.config/xnews/config.json`，数据默认在 `~/.local/share/xnews`。
也可以设置 `XNEWS_CONFIG` 和 `XNEWS_DATA_DIR` 使用独立实例。Markdown 输出目录需要在
配置的 `export.dir` 中填写；`export --all` 和 `reparse` 不需要 token。

开发测试：

```sh
uv run pytest -q
```
