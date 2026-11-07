"""ComfyUI endpoints: import routing, capability inference, request construction, execution.

``kind: comfyui`` definitions combine API workflows/node bindings (``docs/adr/0081`` /
``docs/adr/0082``). Contracts/save validation live in ``arcreel_market_core.comfyui``: structural
``schema.json``, semantic ``validator``, ``bindings`` vocabulary, workflow-reading primitives
``workflow`` / ``graph``. Runtime lives here: :mod:`.import_shapes` routes pasted definitions/API/UI workflows,
:mod:`.inference` infers bindings, :mod:`.capabilities` derives capabilities.

No aggregate exports; consumers import needed submodules. Sole definition-validation entry:
``arcreel_market_core.endpoint_definition.validate_definition``, dispatching by ``kind`` to
``arcreel_market_core.comfyui.validator``.
"""
