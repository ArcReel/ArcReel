"""ComfyUI 端点：定义契约、节点绑定名录与导入分流。

``kind: comfyui`` 的端点定义是一份 API 格式 workflow 加它的节点绑定（``docs/adr/0081`` 与
``docs/adr/0082``）。本包是这种 kind 的实现侧：结构契约在 ``schema.json``，语义判定在
:mod:`.validator`，语义键名录在 :mod:`.bindings`，读 workflow 的原语在 :mod:`.workflow`，
把粘进来的载荷分成定义 / API workflow / UI workflow 三路在 :mod:`.import_shapes`。

本包不再导出聚合入口，消费方直接 import 需要的子模块：定义校验的唯一入口在
``lib.custom_provider.endpoint_definition.validate_definition``，它按 ``kind`` 分派到这里；
把聚合导出写进本 ``__init__`` 会让「分派方 import 实现方」与「实现方 import 诊断载体」在包级
绕成一个环。
"""
