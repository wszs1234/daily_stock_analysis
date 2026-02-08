#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用量监控报表脚本。
查看各用户的深度分析与追问次数统计。
"""
import os
import sys
from pathlib import Path

# 确保项目根目录在 path 中
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from src.usage_tracker import get_user_stats, get_recent_logs


def main():
    print("=" * 60)
    print("用户用量统计")
    print("=" * 60)

    stats = get_user_stats()
    if not stats:
        print("暂无用量记录。")
        return

    # 按用户汇总
    from collections import defaultdict
    by_user = defaultdict(lambda: {"analysis": 0, "follow_up": 0})
    for s in stats:
        uname = s["username"]
        atype = s["action_type"]
        cnt = s["count"]
        by_user[uname][atype] = cnt

    print(f"\n{'用户名':<20} {'深度分析':>10} {'追问次数':>10} {'合计':>10}")
    print("-" * 55)
    for uname in sorted(by_user.keys()):
        d = by_user[uname]
        a = d.get("analysis", 0)
        f = d.get("follow_up", 0)
        print(f"{uname:<20} {a:>10} {f:>10} {a + f:>10}")

    print("\n" + "=" * 60)
    print("最近 20 条记录")
    print("=" * 60)
    for r in get_recent_logs(20):
        print(f"  {r['created_at'][:19]} | {r['username']:<15} | {r['action_type']:<10} | {r['stock_code'] or '-'}")


if __name__ == "__main__":
    main()
