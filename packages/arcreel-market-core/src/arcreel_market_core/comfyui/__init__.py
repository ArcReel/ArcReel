"""ComfyUI endpoint-definition contracts and save-time validation.

Structure contracts live in ``schema.json``, semantic validation in :mod:`.validator`, semantic key catalogs in :mod:`.bindings`,
and workflow-reading primitives in :mod:`.workflow` and :mod:`.graph`. The sole definition-validation entry point is
``arcreel_market_core.endpoint_definition.validate_definition``, which dispatches here by ``kind``.
This package exports no aggregate entry point, avoiding package-level cycles between dispatcher imports of implementations and implementation imports of diagnostic carriers.
"""
