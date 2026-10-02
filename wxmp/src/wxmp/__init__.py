"""wxmp —— 微信公众号订阅采集工具。

设计取舍见 docs/DESIGN.md。三条最重要的约定：

1. 去重主键用文章 URL（含 mid + idx + sn），不用标题、不用时间。
2. 只依赖 requests 一个外部包，其余全部标准库。
3. 代码只写 POSIX 语义，不出现 Windows 路径。
"""

__version__ = "0.1.0"
