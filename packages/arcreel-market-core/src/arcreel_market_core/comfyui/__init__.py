"""ComfyUI 端点定义的契约与保存期判定。

结构契约在 ``schema.json``，语义判定在 :mod:`.validator`，语义键名录在 :mod:`.bindings`，
读 workflow 的原语在 :mod:`.workflow` 与 :mod:`.graph`。定义校验的唯一入口是
``arcreel_market_core.endpoint_definition.validate_definition``，它按 ``kind`` 分派到这里；
本包不导出聚合入口，避免「分派方 import 实现方」与「实现方 import 诊断载体」在包级绕成环。
"""
