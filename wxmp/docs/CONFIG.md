# config.json 全字段参考

## 文件位置

按优先级解析：

1. `$WXMP_CONFIG` —— 显式指定的绝对路径
2. `$XDG_CONFIG_HOME/wxmp/config.json`
3. `~/.config/wxmp/config.json`

`wxmp init` 会在解析出的位置生成一份初始配置。

---

## 完整示例

```jsonc
{
  "version": 1,

  "provider": {
    "name": "tikhub",
    "base_url": "https://api.tikhub.io",
    "token": "你的TikHub密钥",
    "timeout": 120,
    "retry": { "max": 3, "backoff": [2, 5, 15] },
    "rate_limit": { "qps": 1 }
  },

  "schedule": {
    "timezone": "Asia/Shanghai",
    "default_times": ["09:00", "16:00"],
    "catch_up_on_run": true
  },

  "storage": {
    "data_dir": null,
    "keep_raw": true
  },

  "export": {
    "enabled": true,
    "dir": "/path/to/markdown/output",
    "layout": "by_account",
    "filename": "{date} {title}.md",
    "bold_keywords": true,
    "include_images": false
  },

  "accounts": [
    {
      "nick": "仓都加满",
      "username": "mtlsnow",
      "user_name": "gh_e2899e9a812e",
      "media_name": "深圳和光同行传媒有限公司",
      "enabled": true,
      "times": null,
      "first_page_only": true,
      "added_at": "2026-10-02T12:40:00+08:00",
      "last_run_at": null,
      "last_seen_url": null
    }
  ]
}
```

---

## provider — 上游凭据

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `name` | string | `"tikhub"` | 上游名。目前只支持 tikhub |
| `base_url` | string | `"https://api.tikhub.io"` | 接口根地址 |
| `token` | string | `""` | **必填**。TikHub 的 API key，明文存放 |
| `timeout` | int | `120` | 单次请求超时（秒）。正文接口实测要 8–17 秒，别设太小 |
| `retry.max` | int | `3` | 失败重试次数 |
| `retry.backoff` | int[] | `[2,5,15]` | 每次重试前的等待秒数 |
| `rate_limit.qps` | float | `1` | 每秒最多几次请求。设为 `0` 表示不限速 |

**关于 token 明文存放**：这是刻意的。wxmp 是单机自用工具，不做环境变量间接层、
不做密钥管理、不做加密存储 —— 多一层抽象只增加心智负担而不带来实际安全收益。
唯一需要遵守的是：**别把 config.json 提交进 git**。

---

## schedule — 拉取时刻

> ⚠️ **这些字段现在会照常解析和保存，但定时触发尚未实现**（延后到 M6+）。
> 现在请手动跑 `wxmp run`。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `timezone` | string | `"Asia/Shanghai"` | 时刻所属时区 |
| `default_times` | string[] | `["09:00","16:00"]` | 全局默认拉取时刻，24 小时制 |
| `catch_up_on_run` | bool | `true` | 错过的时刻是否在下次启动时补拉 |

时刻支持 `9:00` 这种写法，读入后归一化成 `09:00`。每个账号可以用 `times` 覆盖全局。

---

## storage — 存储

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `data_dir` | string\|null | `null` | 数据目录。`null` = 用 `~/.local/share/wxmp` |
| `keep_raw` | bool | `true` | 是否保留原始响应 JSON |

**`keep_raw` 建议一直开着。** 它是换上游时的救命底牌 ——
微信生态在持续收紧（WeWe RSS 已归档、跨公众号列表接口 2026-07 集体报 `200013`），
只要原始响应还在，换上游后可以无损重建全部衍生数据，不用重抓。
一篇约 10–20 KB，一年几百篇也就几 MB。

> **`data_dir` 放在哪很影响性能**：如果通过 WSL 访问，**不要把 SQLite 放在 `/mnt/c` 上**。
> `/mnt/c` 走 9p 协议，频繁写入会明显卡顿。把它留在 WSL 原生路径（默认值）即可。

---

## export — Markdown 导出

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `false` | 是否在 `run` 之后自动导出 |
| `dir` | string | `""` | **导出目录**。留空则不能导出 |
| `layout` | string | `"by_account"` | `by_account` 按账号建子目录；`flat` 全部平铺 |
| `filename` | string | `"{date} {title}.md"` | 文件名模板 |
| `bold_keywords` | bool | `true` | 是否给股票代码加粗 |
| `include_images` | bool | `false` | 预留，尚未实现 |

`filename` 可用变量：`{date}`（发布日期）、`{title}`、`{account}`、`{id}`。

文件名里的非法字符（`<>:"/\|?*`）会被替换成 `_`。

**`include_images` 目前无效**：正文里的图在 HTML 的 `data-src` 属性上（微信懒加载），
不是 `src`，且 URL 里的 `&` 被转义成 `&amp;` —— 标准 Markdown 转换器一张图都提不出来。
要支持得专门写提取逻辑，v0.1 先不做。原文 HTML 已存进 raw 存档，将来加这个功能不用重抓。

---

## accounts — 订阅列表

| 字段 | 类型 | 说明 |
|---|---|---|
| `nick` | string | 显示名 |
| `username` | string | **必需**。拉取用的标识，`gh_xxx` 或自定义微信号 |
| `user_name` | string | 官方 `gh_` 号，留档用 |
| `media_name` | string | **主体公司名**，辨真伪的关键依据 |
| `enabled` | bool | 停用的号不会被 `run` 处理 |
| `times` | string[]\|null | 号级时刻覆盖。`null` = 用全局 `default_times` |
| `first_page_only` | bool | 日常只拉第一页 |
| `added_at` | string | 添加时间（ISO 8601 带时区） |
| `last_run_at` | int\|null | 上次运行时刻（Unix 秒） |
| `last_seen_url` | string\|null | 预留 |

### 为什么要存 `media_name`

实测搜「仓都加满」返回 14 条候选，其中有：

- 正品：`仓都加满` / `mtlsnow` / **深圳和光同行传媒有限公司**
- 李鬼：`仓满加仓` / `cangmanijiacang` / 个人
- 撇清关系的：`仓都加满不要怂`（简介里写明"本号与公众号'仓都加满'无关"）

**昵称可以被模仿，主体公司名才是辨真伪的依据。** 所以 `wxmp add` 在消歧时会把主体打出来。

---

## 校验规则

配置加载时会检查：

- `version` 必须是 `1`
- `provider.base_url` 必须是合法 URL
- `provider.timeout` 必须为正
- `schedule.default_times` 不能为空
- 每个账号必须有 `username`
- 所有时刻必须是合法 `HH:MM`

任何一条不过会以退出码 `3` 失败，并给出具体是哪个字段。

---

## 写回是原子的

`wxmp` 修改配置时（`add` / `remove` / `schedule set`）会先写同目录的临时文件，
`fsync` 后再 `rename` 覆盖。所以不会出现"配置写到一半断电、config.json 变成半截 JSON"。
