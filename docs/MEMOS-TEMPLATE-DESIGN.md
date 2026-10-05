# Memos 简洁内容模板设计稿

> 状态：已实现。当前 `render.py` 按本模板生成 Memos 内容。

## 1. 目标

让 Memos timeline 中每条资讯尽量短，打开后先看到标题、来源、时间和模型摘要：

- 标题本身直接链接到原文；
- 不再使用 `## 摘要` 标题；
- 不复制完整原文；
- 不把 LLM reasoning 或内部状态写入 memo；
- 去掉当前不可靠的 HTML 注释去重标记。

## 2. 推荐模板

### 微信公众号

```markdown
[人民币资产：价值重估的新阶段](https://mp.weixin.qq.com/s/example)

来源：微信公众号 · 李佳图 · 2026-10-03 12:00

- 人民币在美元走强背景下表现偏强，一度升破 6.70。
- 文章将人民币强势与经常账户/GDP 比率升至高位联系起来。

#infohub #wxmp
```

### X/Twitter

```markdown
[Market outlook for Q4](https://x.com/MacroMargin/status/123456)

来源：X/Twitter · MacroMargin · 2026-10-03 12:00

- 帖子认为利率和流动性将继续影响风险资产表现。
- 作者重点关注美元、债券收益率和科技股之间的联动。

#infohub #xnews
```

## 3. 字段规则

### 3.1 第一行：标题链接

统一使用：

```markdown
[标题](原文 URL)
```

- 微信文章使用文章标题。
- X 帖子使用已有标题；没有标题时使用正文第一行，最多 120 个字符。
- URL 缺失或非法时退化为纯文本标题，不生成空链接。
- 不再使用 H1 标记，避免 timeline 中出现过大的标题层级。

### 3.2 第二段：来源和时间

只保留一行：

```text
来源：<平台/账号> · <发布时间>
```

示例：

```text
来源：微信公众号 · 李佳图 · 2026-10-03 12:00
来源：X/Twitter · MacroMargin · 2026-10-03 12:00
```

没有发布时间时写 `时间未知`，不展示采集时间，避免增加视觉噪音。采集时间仍保存在 `timeline.db`。

### 3.3 摘要正文

直接插入模型的最终 `message.content`，不再包裹 `## 摘要`。模型提示词继续要求输出 2-4 条短要点，因此正文通常是 Markdown 无序列表；模型返回段落时也原样保留。

只插入非空的最终摘要：

- 不插入 `reasoning_content`；
- 空摘要不发布到 Memos；
- 原文正文不复制到 Memos，原文通过第一行标题链接访问。

### 3.4 标签

文末保留一行 Memos 标签：

```text
#infohub #wxmp
```

标签来自配置中的 `memos.tags`，并自动追加来源标签 `wxmp` 或 `xnews`。标签用于 Memos 的筛选和搜索，不承担主键或幂等作用。

## 4. 去重和状态

模板中不再写 HTML 注释标记。原因是当前 Memos 版本会把 HTML 注释内容解析成文档节点，不能保证它在界面上隐藏。

去重完全依赖本地 `timeline.db`：

```text
item_key + summary_id + target=memos
```

同一条摘要已经发布时跳过；远端 `name`、`uid` 和正文哈希仍写入 `deliveries`。这保持正文简洁，但进程在“远端创建成功、本地状态尚未写入”这个极小窗口崩溃时，可能需要人工对账。Memos 列表接口当前也不适合承担强一致的远端查重。

## 5. 配置和可见性

- `visibility` 继续从 `memos.visibility` 读取，默认 `PRIVATE`。
- `memos.tags` 默认 `["infohub"]`。
- 模板不展示模型名称、usage、耗时、摘要版本或内部错误。
- 这些运维字段继续保存在本地 `timeline.db` 和 LLM raw JSON 中。

## 6. 已完成的代码修改

1. `src/infohub/render.py`：已改用标题链接、简化来源行、移除 H1/`## 摘要`/HTML 注释。
2. `tests/test_render.py`：已覆盖链接、来源行、平台标签和没有内部标记。
3. `docs/INFOHUB-DESIGN.md`：已同步 Memos 内容示例和去重说明。

Qwen 请求、SQLite schema、采集流程和 Memos API 请求体不变；摘要提示词现在从 `llm.system_prompt` 和 `llm.user_prompt_template` 配置字段读取。
