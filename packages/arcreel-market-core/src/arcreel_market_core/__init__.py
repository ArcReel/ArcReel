"""ArcReel market source tools and endpoint definition validators.

Market sources (``market``): address parsing, index schema and projection, entry validation, index generation, icon checks, and directory validation;
endpoint definitions (``endpoint_definition`` and dependencies): structural and semantic validation. Diagnostics always use ``code + params``;
this package has no translation catalog, and callers supply translators for rendering.
"""
