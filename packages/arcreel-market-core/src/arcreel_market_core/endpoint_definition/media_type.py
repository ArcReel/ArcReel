"""Read a definition's media type, sharing one implementation across index projections, endpoint projections, and mirror columns."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .kinds import COMFYUI_KIND, DECLARATIVE_KIND

#: Media type when a declarative definition omits ``media_type``. Existing and bundled definitions without this field are video definitions,
#: so the default must be video.
DEFAULT_DECLARATIVE_MEDIA_TYPE = "video"

#: ``kind`` to media-type reader. Both kinds declare media type in the definition; ComfyUI requires it explicitly.
_MEDIA_TYPE_BY_KIND: Mapping[str, Callable[[Mapping[str, Any]], str]] = {
    DECLARATIVE_KIND: lambda definition: str(definition.get("media_type", DEFAULT_DECLARATIVE_MEDIA_TYPE)),
    COMFYUI_KIND: lambda definition: str(definition["media_type"]),
}


def definition_media_type(definition: Mapping[str, Any]) -> str:
    """Read media type from a **validated** definition according to ``kind``.

    Raises:
        KeyError: Definition lacks ``kind``, or a ComfyUI definition lacks ``media_type``.
        ValueError: Definition ``kind`` has no media-type reader.
    """
    kind = str(definition["kind"])
    read = _MEDIA_TYPE_BY_KIND.get(kind)
    if read is None:
        raise ValueError(f"unsupported endpoint definition kind: {kind!r}")
    return read(definition)
