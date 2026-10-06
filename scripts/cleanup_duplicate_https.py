#!/usr/bin/env python3
"""清理 timeline.db 中重复的 https:// 条目（由 canonical_wechat_url 改动导致）"""

import sqlite3
import shutil
from pathlib import Path


def main():
    db_path = Path.home() / ".local/share/infohub/timeline.db"
    backup_path = db_path.parent / "timeline.db.backup-before-cleanup"

    if not db_path.exists():
        print(f"数据库不存在: {db_path}")
        return 1

    # 备份数据库
    print(f"备份数据库到: {backup_path}")
    shutil.copy2(db_path, backup_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 统计要删除的数据
    https_items = cur.execute(
        "SELECT COUNT(*) FROM items WHERE item_key LIKE 'wxmp:https://%'"
    ).fetchone()[0]
    https_deliveries = cur.execute(
        "SELECT COUNT(*) FROM deliveries WHERE item_key LIKE 'wxmp:https://%'"
    ).fetchone()[0]
    https_summaries = cur.execute(
        "SELECT COUNT(*) FROM summaries WHERE item_key LIKE 'wxmp:https://%'"
    ).fetchone()[0]

    print("\n将删除:")
    print(f"  items: {https_items}")
    print(f"  deliveries: {https_deliveries}")
    print(f"  summaries: {https_summaries}")

    if https_items == 0:
        print("\n没有需要清理的条目")
        conn.close()
        return 0

    # 删除 https 版本
    cur.execute("DELETE FROM deliveries WHERE item_key LIKE 'wxmp:https://%'")
    cur.execute("DELETE FROM summaries WHERE item_key LIKE 'wxmp:https://%'")
    cur.execute("DELETE FROM items WHERE item_key LIKE 'wxmp:https://%'")

    conn.commit()

    # 验证
    remaining_https = cur.execute(
        "SELECT COUNT(*) FROM items WHERE item_key LIKE 'wxmp:https://%'"
    ).fetchone()[0]
    remaining_http = cur.execute(
        "SELECT COUNT(*) FROM items WHERE item_key LIKE 'wxmp:http://%'"
    ).fetchone()[0]

    print("\n清理后:")
    print(f"  wxmp:https:// 剩余: {remaining_https}")
    print(f"  wxmp:http:// 剩余: {remaining_http}")

    conn.close()
    print(f"\n✅ 清理完成！备份保存在: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
