# 在 WSL 里使用

wxmp 的代码只写 POSIX 语义（见 [DESIGN.md](DESIGN.md)），
所以在 WSL 里跑很自然。但有几个 WSL 特有的坑要注意。

---

## 1. 从 Windows 盘访问项目

如果项目放在 Windows 盘上，WSL 里的对应路径是：

```
Windows:  C:\Users\qiumi\Documents\Alma\...\outputs\wxmp
WSL:      /mnt/c/Users/qiumi/Documents/Alma/.../outputs/wxmp
```

---

## 2. ⚠️ 数据目录不要放 `/mnt/c`

**这是最重要的一条。** `/mnt/c` 通过 9p 协议挂载，比 WSL 原生文件系统慢很多，
而 SQLite（尤其开了 WAL 模式）会频繁写入 —— 放上去会明显卡顿。

**推荐布局**：

| 内容 | 位置 | 原因 |
|---|---|---|
| 代码 | `/mnt/c/...`（Windows 盘） | 方便在 Windows 侧编辑查看 |
| **数据目录** | **`~/.local/share/wxmp`（WSL 原生）** | 避免 9p 性能问题 |
| Markdown 导出 | `/mnt/c/...`（Windows 盘） | 要挂到 Obsidian 等工具，跑不掉；一次性写入不卡 |

这是默认行为，**不需要额外配置** —— 只要别把 `storage.data_dir` 改成 `/mnt/c` 就行。

---

## 3. `uv` 不在 PATH 里

从 Windows 侧用 `wsl.exe bash -c "..."` 调用时，非登录 shell 不会加载 `~/.profile`，
所以 `~/.local/bin/uv` 可能找不到。

**对策**：在脚本开头显式补 PATH：

```sh
export PATH="$HOME/.local/bin:$PATH"
```

---

## 4. 从 Windows 侧执行长命令时的引号陷阱

`wsl.exe bash -c "..."` 会被 Git Bash/MSYS 二次改写参数，反斜杠会被吃掉，
嵌套引号也会乱掉。**长脚本请写成文件再传**：

```sh
# 不推荐：嵌套引号容易出错
wsl.exe bash -c 'cd /mnt/c/... && python -c "import x"'

# 推荐：写脚本文件，用 MSYS_NO_PATHCONV 关掉路径改写
cat > /tmp/run.sh <<'EOS'
cd /mnt/c/Users/qiumi/Documents/Alma/.../outputs/wxmp
export PATH="$HOME/.local/bin:$PATH"
wxmp run
EOS
MSYS_NO_PATHCONV=1 wsl.exe bash /tmp/run.sh
```

`MSYS_NO_PATHCONV=1` 用来阻止 Git Bash 把 `/mnt/c/...` 改写成 `C:/Program Files/Git/mnt/c/...`。

---

## 5. 定时任务在 WSL 里跑不了（M6+ 议题）

> 本节记录一个**已知的未来问题**。定时触发功能本身延后到 M6+ 实现，
> 但如果你现在想自己写 crontab，请先读这一节。

**WSL2 默认不启用 systemd，而且不跑 crontab 服务** —— 光写一条 crontab 是死的，
不会有任何东西去执行它。

**可行方案**：在 **Windows 侧**建计划任务，调用 WSL 执行：

```
wsl.exe -d Ubuntu -- bash -lc "cd /path && wxmp run"
```

这是 WSL 里唯一能真正跑准时的路子。M6+ 实现 `wxmp schedule install` 时会生成这个。

---

## 6. Node 环境（与核心无关）

WSL 里 Node 是通过 nvm 装的（目前 v24.20.0），**默认不在 PATH 上**。需要时：

```sh
source ~/.nvm/nvm.sh
```

wxmp 核心是 Python，不依赖 Node。Node 只在将来做局域网只读查询站时才会用到。

---

## 环境自检

```sh
# 在 WSL 里
export PATH="$HOME/.local/bin:$PATH"
uv --version          # 需要 uv
python3 --version     # 需要 3.12+

# 确认 FTS5 可用（wxmp 的中文检索依赖它）
python3 -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute(\"create virtual table t using fts5(x)\"); print('FTS5 OK', sqlite3.sqlite_version)"
```

CPython 官方构建都自带 FTS5。如果这行报错，说明用的是某个裁剪过的 Python，
换用官方构建或 `uv python install 3.12` 装的版本即可。
