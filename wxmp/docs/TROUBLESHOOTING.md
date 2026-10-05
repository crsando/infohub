# 常见错误与排查

## HTTP 403 / 被 Cloudflare 拦截

**症状**：`错误: 上游拒绝请求 (HTTP 403)`，退出码 4。

**原因**：**这是最经典的坑。** 不带 `User-Agent` 的请求会被 Cloudflare 直接挡掉，
用 Python 默认 UA 必然失败。

**排查**：

```sh
wxmp check          # 会实际发一次请求验证
```

**说明**：wxmp 已经在 `api.py` 里恒带 `User-Agent: Mozilla/5.0 ...`，
所以正常情况下不会再撞这个坑。如果仍然 403，可能是：

- token 已失效或被 revoke
- 上游临时风控

先用 `wxmp check` 区分这两者。`check` 里 token 会打码显示，可以确认读到的是不是你预期的那个。

---

## 401 / token 无效

**症状**：`错误: 凭据无效 (HTTP 401)`，退出码 4。

**排查**：

1. 确认 `TIKHUB_TOKEN` 环境变量或 config.json 的 `provider.token` 至少有一处非空；环境变量优先
2. 到上游后台确认 key 还有效、额度没耗尽
3. `wxmp check` 看打码后的 token 前后几位对不对

---

## 配置文件找不到

**症状**：`错误: 找不到配置文件: /path/to/config.json`，退出码 3。

**排查**：

```sh
echo $WXMP_CONFIG        # 看环境变量指向哪
wxmp init              # 没有就生成一份
```

注意 `WXMP_CONFIG` 指向的是 **config.json 这个文件本身**，不是目录。

---

## 搜「港股」搜不到，但「南向资金」能搜到

**这不是 bug。** FTS5 的 trigram 分词器**只索引 3 字符及以上的片段**，
两字词用 FTS5 检索会返回 0 条。

wxmp 已经按关键词长度自动分流：≥3 字走 FTS5，≤2 字退回 `LIKE` 扫描。
所以正常情况下两种都能搜到。

**如果真的两字词搜不到**，检查：

```sh
wxmp stats             # 看文章数是不是 0
wxmp show --limit 5    # 看库里到底有没有文章
```

---

## 同一篇文章重复入库 / 每次都报"新增 N 篇"

**症状**：连着跑两次 `wxmp run`，两次都报新增相同的篇数。

**原因**：上游返回的文章 URL 里 **`chksm` 参数每次请求都不同**，
如果直接拿完整 URL 当去重主键，同一篇文章每轮都会被当成新文章。

**说明**：wxmp 已在 `store.canonical_url()` 里把 URL 归一化到
`__biz + mid + idx + sn` 四个参数，丢弃其余噪声参数。正常情况下不会出现这个问题。

**如果仍然重复**：

```sh
# 看 URL 是不是真的被归一化了
sqlite3 ~/.local/share/wxmp/wxmp.db \
  "select url, count(*) from articles group by url having count(*)>1"
```

正常应该只看到 `__biz&mid&idx&sn` 四个参数。

---

## 账号搜索命中了错误的号

**症状**：`wxmp add 某某号` 订阅到了同名的李鬼账号。

**原因**：公众号昵称可以被模仿，实测搜一个词返回十几个候选，
里面有正品、有蹭名的、有撇清关系的。

**对策**：

```sh
wxmp add 某某号              # 交互式会列出候选表让你选
wxmp add 某某号 --pick 3     # 直接指定第 3 个
wxmp add 某某号 --media "深圳和光同行传媒有限公司"   # 用主体公司名限定
```

**认主体公司名，不要认昵称。** 这就是候选表里一定要打印 `media_name` 的原因。

已经加错了就：

```sh
wxmp remove 加错的标识 --purge
```

---

## 采集很慢（每篇 8–17 秒）

**这是正常的。** 瓶颈在上游的文章详情接口，实测单篇 8–17 秒、且不稳定。

一轮里只有**新文章**才需要抓详情，所以：

- 日常没有新文章时，整轮只需约 2.5 秒
- 如果是首次回采（一次 10 篇），一轮 90 秒左右是预期值

`--pages` 调大会显著拉长时间，回采时请有心理准备。

---

## WSL 里 SQLite 很卡

**原因**：通过 WSL 访问 `/mnt/c` 走的是 9p 协议，比 WSL 原生文件系统慢很多，
SQLite 频繁写入会明显卡顿。

**对策**：**把数据目录留在 WSL 原生路径**（默认的 `~/.local/share/wxmp`），
不要设成 `/mnt/c/...`。

Markdown 导出目录可以放 `/mnt/c`（要挂到 Obsidian 之类的工具里，跑不掉），
但那里只有一次性写入，不会卡。

详见 [WSL.md](WSL.md)。

---

## 导出目录未配置

**症状**：`错误: export.dir 未配置`，或跑完 `run` 没有任何 Markdown 产出。

**对策**：编辑 config.json：

```jsonc
"export": {
  "enabled": true,
  "dir": "/绝对/路径/到输出目录"
}
```

`dir` 必须是绝对路径或 `~` 开头的路径。目录不存在会自动创建。

---

## 需要人工消歧（退出码 6）

**症状**：`错误: 关键词「X」命中 N 个候选，需要人工消歧`

**原因**：在非交互环境（cron、脚本）里 `add` 遇到多个候选。
wxmp 刻意**不猜** —— 猜错会静默订阅到李鬼号，比直接失败糟糕得多。

**对策**：

```sh
wxmp add "X" --pick 2                              # 指定第几个
wxmp add "X" --media "主体公司名"                    # 用主体限定
wxmp add gh_xxxxxxxx --nick X                      # 直接用标识，最稳
```

---

## 想清空重来

```sh
rm -rf ~/.local/share/wxmp     # 删库和原始存档（不可恢复）
wxmp init                    # 重建
```

只想删某个号的文章：

```sh
wxmp remove <key> --purge
```

---

## 其他

**看调试日志**：

```sh
wxmp -v run
```

会打印每篇文章的抓取记录，便于定位卡在哪一步。

**看库里的原始状态**：

```sh
sqlite3 ~/.local/share/wxmp/wxmp.db "select count(*) from articles"
```
