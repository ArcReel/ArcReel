"""backend_assembly: unified construction seam from provider config + model → backend.

Expose only assemble_backend, routing built-in/custom families through is_custom_provider. Callers reusing backend
instances separately check callable_model_key against the current lifecycle before fetching cached instances.
The families share an entry point, not table structures (docs/adr/0039): built-ins use ProviderSpec closure tables (specs.py),
while custom providers delegate to existing ENDPOINT_REGISTRY (lib/custom_provider/endpoints.py).
"""

from __future__ import annotations

from lib.backends.backend_assembly.assembler import assemble_backend, callable_model_key

__all__ = ["assemble_backend", "callable_model_key"]
