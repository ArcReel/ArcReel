"""声明式定价：类型（``types``）、按 kind 计算策略（``strategies``）、按 provider/model 查表（``lookup``）。

各模块独立可导入；消费方按需 ``from lib.pricing.lookup import lookup_pricing`` 等显式引用子模块。
"""

from __future__ import annotations
