# 命令参考

单一入口 `wxmp`，所有功能挂子命令。

> 下文示例写作 `wxmp …`。在项目目录里未安装时，等价于 `uv run wxmp …`。

```
wxmp [-v|--verbose] [--version] <命令> [参数]
```

`-v` 输出调试日志（含每篇文章的抓取记录）。

---

## wxmp init

初始化配置与数据目录。

| 参数 | 说明 |
|---|---|
| `--force` | 覆盖已存在的配置 |

在 `$WXMP_CONFIG`（或缺省位置）生成 `config.json`，并创建数据目录。

```sh
wxmp init
```

---

## wxmp add

新增订阅。`<目标>` 支持三种形态，解析方式完全不同：

### ① 按名称（走搜索，需要消歧）

```sh
wxmp add 仓都加满
```

命中唯一则直接加入；命中多条则**打印候选表要求确认**。

候选表会把**主体公司名**打出来 —— 这是辨真伪的关键，因为昵称可以被模仿。
非交互环境（cron）遇到多个候选会**直接失败**（退出码 6），绝不猜。

### ② 按文章 URL（无歧义）

```sh
wxmp add "https://mp.weixin.qq.com/s/xxxxx"
```

从 URL 直接读 `__biz`，再通过文章详情定位账号，全程不需要搜索。

### ③ 按标识（最快）

```sh
wxmp add mtlsnow --nick 仓都加满
wxmp add gh_e2899e9a812e --nick 仓都加满
```

不搜索，但仍然会调一次资料接口**核实账号存在**，顺便补全昵称与原创篇数。

> 判据是"是否全为 ASCII 字母数字下划线连字符"：公众号 username 一律是 ASCII，
> 中文一定是昵称。所以 `仓都加满` 不会被误当成标识。

| 参数 | 说明 |
|---|---|
| `--nick <名>` | 自定义显示名 |
| `--media <主体>` | 限定主体公司名以消歧 |
| `--pick <N>` | 直接指定第 N 个候选（1 起），跳过交互 |
| `--time <HH:MM>` | 号级拉取时刻，可重复给 |
| `--pages <N>` | 首次抓取翻页数（默认 1） |
| `--no-fetch` | 只加订阅，不立即抓 |

---

## wxmp remove

```sh
wxmp remove mtlsnow            # 移出订阅，已抓文章保留
wxmp remove mtlsnow --purge    # 连库里的文章一起删
```

| 参数 | 说明 |
|---|---|
| `--keep-data` | 保留已抓文章（默认） |
| `--purge` | 同时删除库中该号的文章 |

---

## wxmp list

```sh
wxmp list
wxmp list --json
```

显示每个订阅的启用状态、主体、生效时刻（并标注是号级覆盖还是全局默认）。

---

## wxmp enable / disable

```sh
wxmp disable 仓都加满
wxmp enable 仓都加满
```

停用的号不会被 `wxmp run` 处理，但文章仍在库里。

---

## wxmp schedule

> ⚠️ **定时触发尚未实现。** 这组命令只维护配置，不会真的到点执行。

```sh
wxmp schedule                          # 查看当前时刻表
wxmp schedule set 09:00 16:00          # 设全局默认
wxmp schedule set 08:30 --account 仓都加满   # 给某个号单独设
wxmp schedule unset --account 仓都加满       # 清除号级覆盖
```

`install` 动作已被识别但会明确报"尚未实现"，退出码 2 ——
**明确失败，而不是静默不生效**。

---

## wxmp run

手动跑一轮采集。

```sh
wxmp run                          # 跑全部启用的号
wxmp run --account 仓都加满        # 只跑指定号，可重复
wxmp run --pages 3                # 翻 3 页（首次回采用）
wxmp run --dry-run                # 只列将处理的账号，不发请求
```

| 参数 | 说明 |
|---|---|
| `--account <key>` | 只跑指定账号，可重复 |
| `--pages <N>` | 翻页数，默认 1 |
| `--dry-run` | 不发任何请求，只确认配置 |

**单账号失败不会中断整轮**，会记下错误继续下一个，最后以退出码 5 结束。

跑完会自动导出 Markdown（若 `export.enabled` 且 `export.dir` 已配置）。

### 成本

按设计，日常轮询只拉第一页（约 10 条），只有新文章才抓正文：

- 无新文章：**1 次调用 / 约 2.5 秒**
- 有新文章：**每条 2 次调用 / 每条约 9 秒**

---

## wxmp search

全文检索。

```sh
wxmp search 南向资金
wxmp search 港股 --limit 5
wxmp search 港股 --since 7d
wxmp search 限购 --account 仓都加满 --json
```

| 参数 | 说明 |
|---|---|
| `--account <key>` | 限定账号 |
| `--since <时间>` | `2026-01-01` / `7d` / `24h` 三种写法 |
| `--limit <N>` | 默认 20 |
| `--json` | JSON 输出 |

### 检索行为

**关键词长度决定走哪条路，自动选择：**

| 长度 | 实现 | 行为 |
|---|---|---|
| ≥ 3 字 | FTS5 + trigram | 带 `<<高亮>>` 摘要，快 |
| ≤ 2 字 | `LIKE` 扫描 | 手写摘录，扫全表但仍是毫秒级 |

原因是 FTS5 的 trigram 分词器**只索引 3 字符及以上的片段**，
搜「港股」用 FTS5 会返回 0 条 —— 不是没数据，是索引根本不覆盖。

---

## wxmp show

```sh
wxmp show --limit 10
wxmp show --account 仓都加满
```

列出最近文章及导出状态。

---

## wxmp export

```sh
wxmp export                    # 只导出还没导出的
wxmp export --all              # 重新导出全部
wxmp export --since 2026-01-01
wxmp export --account 仓都加满
```

| 参数 | 说明 |
|---|---|
| `--since <时间>` | 起始时间 |
| `--account <key>` | 限定账号 |
| `--all` | 重新导出全部（含已导出的） |

产出见 [CONFIG.md](CONFIG.md) 的 export 一节，以及 [DESIGN.md](DESIGN.md) 的导出设计。

---

## wxmp stats

```sh
wxmp stats
```

显示订阅数、文章数、库大小、检索可用性、最近运行时间、最近错误。

---

## wxmp check

体检。会实际发一次上游请求验证连通性（消耗 1 次调用）。

```sh
wxmp check
```

检查项：

- 配置文件存在且可解析
- token 已配置（**输出时打码**）
- 导出目录可写（实际写一个测试文件再删掉）
- 数据目录可写
- 数据库可读写、FTS5 可用
- 每个订阅的标识
- 上游连通性

全部通过返回 0，有问题返回 3。

---

## 退出码

| 码 | 含义 | 典型场景 |
|---|---|---|
| 0 | 成功 | |
| 1 | 一般错误 | 用户在交互中取消 |
| 2 | 参数错误 | `schedule install`（未实现）、时间格式错 |
| 3 | 配置错误 | 配置文件缺失/非法、账号已订阅 |
| 4 | 凭据错误 | token 无效、被 Cloudflare 拦截 |
| 5 | 上游错误 | 网络失败、上游 5xx、重试耗尽 |
| 6 | 需要人工消歧 | 多候选且非交互环境 |

脚本化调用时可以靠它分支处理：

```sh
if ! wxmp run; then
  case $? in
    4) echo "token 有问题" ;;
    5) echo "上游挂了" ;;
  esac
fi
```
