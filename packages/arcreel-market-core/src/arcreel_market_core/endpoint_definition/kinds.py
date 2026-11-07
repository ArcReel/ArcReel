"""Endpoint-definition container kinds: shared names for every kind-based dispatcher.

``kind`` is a top-level definition JSON field determining invocation form and whose implementation handles subsequent
steps: structural/semantic validation, ``EndpointSpec`` projection, mirror-column media types, and supported endpoint-test modes. Each dispatch
point owns a kind-to-implementation map whose keys are its recognized kinds; container validation accepts only kinds with validators.

This module contains constants only and imports no repository modules. Dispatch spans validation, projection, and routes; shared constants prevent
layers from silently running with misspelled kind names.
"""

from __future__ import annotations

#: Declarative definitions: JSON in/out plus submit/poll, with request templates and extraction paths in the definition (``docs/adr/0067``).
DECLARATIVE_KIND = "declarative"

#: ComfyUI definitions: an API-format workflow and its node bindings (``docs/adr/0081``).
COMFYUI_KIND = "comfyui"
