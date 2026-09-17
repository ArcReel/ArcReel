"""端点定义的容器 kind：按 kind 分派的各处共用同一批名字。

``kind`` 是定义 JSON 的顶层字段，决定这份定义描述的是哪种调用形态，也决定后面每一步走谁的
实现：结构与语义校验、``EndpointSpec`` 投影、镜像列的媒体类型、端点测试支持哪几种模式。各分派
点自带「kind → 实现」的表，表的键集就是那一层认得的 kind；容器层校验只放行有校验实现的 kind。

本模块只有常量，不引任何仓库内模块——分派点散落在校验器、投影与路由三层，共用同一批常量才不会
出现某一层把 kind 名拼错还照跑的情形。
"""

from __future__ import annotations

#: 声明式定义：「JSON in/out + 提交/轮询」流派，请求模板与取值路径都写在定义里（``docs/adr/0067``）。
DECLARATIVE_KIND = "declarative"

#: ComfyUI 定义：一份 API 格式 workflow 连同它的节点绑定（``docs/adr/0081``）。
COMFYUI_KIND = "comfyui"
