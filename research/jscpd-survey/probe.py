#!/usr/bin/env python3
"""按文件名子串过滤，打印各档 jscpd 报告里涉及该文件的 clone 片段。

用法: python3 research/jscpd-survey/probe.py <文件名子串> <k...>
"""

from __future__ import annotations

import json
import sys

sub = sys.argv[1]
for k in sys.argv[2:]:
    d = json.load(open(f"research/jscpd-survey/raw/k{k}/jscpd-report.json", encoding="utf-8"))
    print(f"=== k={k} ===")
    for x in d["duplicates"]:
        f1, f2 = x["firstFile"], x["secondFile"]
        if sub in f1["name"] or sub in f2["name"]:
            print(
                f"  {f1['name']}[{f1['start']}:{f1['end']}] <-> "
                f"{f2['name']}[{f2['start']}:{f2['end']}]  {x['lines']}L {x['tokens']}T"
            )
