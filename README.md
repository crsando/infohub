# infohub

本目录存放 TiKHub 相关的采集工具、实验代码和研究资料。

| 路径 | 用途 | 状态 |
| --- | --- | --- |
| [wxmp/](wxmp/) | 微信公众号订阅、全文采集、SQLite 检索和 Markdown 导出 | 可用；31 项测试通过 |
| [demo/tikhub-x-latest/](demo/tikhub-x-latest/) | 读取 X/Twitter 账号最新帖子的 uv demo | 接口路径待实际联通验证 |
| [tikhub-api-notes.md](tikhub-api-notes.md) | TiKHub 通用调用与三平台研究笔记 | 含尚未实时核实的路径示例 |

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

运行时配置、API token、SQLite 数据库和原始响应属于本地数据，不应提交到仓库。`wxmp` 的配置文件默认位于 `~/.config/wxmp/config.json`，初始配置和权限要求见 [wxmp/README.md](wxmp/README.md)。
