---
status: accepted
---

# 调用通道共用构造入口，按供应商类型装配

文本工厂和媒体任务曾各自拼装凭证、模型名和供应商地址，同一供应商的调用行为容易在入口之间分叉。统一由 [`assemble_backend`](../../lib/backends/backend_assembly/assembler.py) 装配调用通道。数据库配置装载与通道构造分开，调用方可以复用同一入口并管理自己的实例缓存。

内置供应商按供应商、媒体类型及挂接端点选择构造声明，可接收供应商专用凭证。自定义供应商按其模型挂接的调用端点构造，使用统一的单密钥连接模型。两套声明各自配合其凭证、端点和数据来源，共享构造入口。内置端点选择及版本约束见 [ADR 0103](0103-catalog-data-repo-built-from-third-party-sources.md)，自定义端点定义见 [ADR 0067](0067-custom-endpoint-declarative-definition.md)。
