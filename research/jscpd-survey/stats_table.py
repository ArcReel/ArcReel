#!/usr/bin/env python3
"""汇总各 min-tokens 档的 jscpd 统计成一张表。

用法: python3 research/jscpd-survey/stats_table.py 10 25 50 75 100 150 200
"""

from __future__ import annotations

import json
import sys

print("| min-tokens | 分析文件 | clone 对 | 重复行 | 重复行占比 | 重复 token 占比 |")
print("| ---: | ---: | ---: | ---: | ---: | ---: |")
for k in sys.argv[1:]:
    t = json.load(open(f"research/jscpd-survey/raw/k{k}/jscpd-report.json", encoding="utf-8"))["statistics"]["total"]
    print(
        f"| {k} | {t['sources']} | {t['clones']} | {t['duplicatedLines']} | "
        f"{t['percentage']:.2f}% | {t['percentageTokens']:.2f}% |"
    )
