"""backend_assembly — 「provider config + model → backend」的统一构造缝。

暴露唯一入口 assemble_backend，内部按 is_custom_provider 分流到内置 / 自定义两族；复用 backend 实例的调用方
取缓存前另用 callable_model_key 按今天的生命周期检查。
两族共享入口、不共享表结构（见 docs/adr/0039）：内置侧用 ProviderSpec 闭包表（specs.py），
自定义侧委托现成 ENDPOINT_REGISTRY（lib/custom_provider/endpoints.py）。
"""

from __future__ import annotations

from lib.backends.backend_assembly.assembler import assemble_backend, callable_model_key

__all__ = ["assemble_backend", "callable_model_key"]
