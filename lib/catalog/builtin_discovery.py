"""Built-in model discovery: fetch provider lists, taking IDs only and matching manifest API names exactly (docs/adr/0105).

Seven built-ins expose lists: gemini-aistudio/gemini-vertex use google shapes; openai/grok/dashscope/minimax/
agnes use OpenAI-compatible shapes. Ignore other returned metadata; discovery never determines callability because account/region affect results.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from arcreel_catalog_core import CatalogEntry
from lib.catalog import query as catalog
from lib.catalog.model_id import has_dot_segment
from lib.custom_provider.endpoints import infer_media_type

DiscoveryForm = Literal["google", "openai"]

#: Built-in providers with model-list interfaces and their formats.
BUILTIN_DISCOVERY_FORMS: Mapping[str, DiscoveryForm] = {
    "gemini-aistudio": "google",
    "gemini-vertex": "google",
    "openai": "openai",
    "grok": "openai",
    "dashscope": "openai",
    "minimax": "openai",
    "agnes": "openai",
}

#: xAI compatible API address; grok channels use xai_sdk without stored addresses.
XAI_OPENAI_BASE_URL = "https://api.x.ai/v1"


class BuiltinDiscoveryConfigError(ValueError):
    """Missing discovery credential fields, such as absent Vertex files/project_id. ``code`` is i18n keys."""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass(frozen=True)
class DiscoveredBuiltinModel:
    """One discovered model.

    True ``recognized`` means official manifest matches; ``model_key`` is corresponding keys (dual-modality API names matching two
    keys produce separate rows). Otherwise ``model_key`` is ``None``; ``media_type`` uses matching entries, then name inference,
    serving user-added model media. ``entry`` is name-matched, ``None`` if absent; added calls price through it.
    """

    model_id: str
    media_type: str
    recognized: bool
    model_key: str | None
    entry: CatalogEntry | None


def is_discoverable(provider_id: str) -> bool:
    return provider_id in BUILTIN_DISCOVERY_FORMS


def match_by_name(provider_id: str, model_id: str) -> tuple[str, CatalogEntry | None]:
    """Without official model matches, resolve name-based entries/media (docs/adr/0105).

    Direct entry matches supply media. Dual-modality names cannot identify one entry, so infer media by name, then query again
    within that media. This matches added user-model ``catalog.find_entry`` semantics, ensuring discovered entries
    are exactly those used for pricing after addition.
    """
    entry = catalog.find_entry(model_id)
    if entry is not None:
        return entry.media_type, entry
    media_type = infer_media_type(model_id, BUILTIN_DISCOVERY_FORMS.get(provider_id, "openai"))
    return media_type, catalog.find_entry(model_id, media_type=media_type)


def name_media_type(provider_id: str, model_id: str) -> str:
    """Without official matches, use name-matched entry media, otherwise name inference shared with custom discovery."""
    return match_by_name(provider_id, model_id)[0]


def classify_model_ids(provider_id: str, model_ids: list[str]) -> list[DiscoveredBuiltinModel]:
    """Match exact manifest API model names; sort/deduplicate by model ID."""
    official: dict[str, list[catalog.ProviderModelView]] = {}
    for view in catalog.provider_models(provider_id):
        if view.official:
            official.setdefault(view.api_model_name, []).append(view)
    result: list[DiscoveredBuiltinModel] = []
    # Dot-segment IDs fail addition (model_id.py); exclude from addable lists.
    for model_id in sorted({m for m in model_ids if not has_dot_segment(m)}):
        views = official.get(model_id)
        if views:
            result.extend(
                DiscoveredBuiltinModel(model_id, view.model.media_type, True, view.model_id, view.entry)
                for view in views
            )
        else:
            media_type, entry = match_by_name(provider_id, model_id)
            result.append(DiscoveredBuiltinModel(model_id, media_type, False, None, entry))
    return result


def list_model_ids(provider_id: str, config: Mapping[str, str]) -> list[str]:
    """Fetch provider lists and return IDs; synchronous network requests require caller threads.

    ``config`` overlays provider credentials (``api_key``, ``base_url``, ``credentials_path``).

    Raises:
        BuiltinDiscoveryConfigError: Missing credential fields needed for discovery.
        ValueError: No model-list interface for ``provider_id``.
    """
    match provider_id:
        case "gemini-aistudio":
            return _list_google_aistudio(config)
        case "gemini-vertex":
            return _list_google_vertex(config)
        case "openai" | "grok" | "dashscope" | "minimax" | "agnes":
            return _list_openai(config["api_key"], _openai_base_url(provider_id, config.get("base_url")))
        case _:
            raise ValueError(f"provider {provider_id!r} has no model list API")


def _openai_base_url(provider_id: str, configured: str | None) -> str:
    """Explicit model-list addresses per provider. ``None`` would make OpenAI SDK read ``OPENAI_BASE_URL`` and send keys there."""
    match provider_id:
        case "grok":
            return XAI_OPENAI_BASE_URL
        case "dashscope":
            from lib.backends.dashscope_shared import dashscope_text_base_url

            return dashscope_text_base_url(configured)
        case "minimax":
            from lib.backends.minimax_shared import minimax_text_base_url

            return minimax_text_base_url(configured)
        case "agnes":
            from lib.backends.agnes_shared import agnes_base_url

            return agnes_base_url(configured)
        case _:
            from lib.config.url_utils import OFFICIAL_OPENAI_BASE_URL

            return (configured or "").strip() or OFFICIAL_OPENAI_BASE_URL


#: Per-list-call timeout; callers have total deadlines, so terminate threads instead of waiting for long SDK defaults/retries.
_LIST_TIMEOUT_SECONDS = 30
#: Same timeout in genai SDK ``HttpOptions.timeout`` milliseconds; without ``retry_options``, it does not retry.
_GENAI_LIST_TIMEOUT_MS = _LIST_TIMEOUT_SECONDS * 1000


def _list_openai(api_key: str, base_url: str) -> list[str]:
    from openai import OpenAI

    with OpenAI(api_key=api_key, base_url=base_url, timeout=_LIST_TIMEOUT_SECONDS, max_retries=0) as client:
        return [m.id for m in client.models.list() if m.id]


def _short_name(name: str | None) -> str:
    """Strip path prefixes such as ``models/`` / ``publishers/google/models/``."""
    return (name or "").rsplit("/", 1)[-1]


def _list_google_aistudio(config: Mapping[str, str]) -> list[str]:
    from google import genai

    from lib.config.url_utils import ensure_google_base_url

    http_options: genai.types.HttpOptionsDict = {"timeout": _GENAI_LIST_TIMEOUT_MS}
    if base_url := config.get("base_url"):
        http_options["base_url"] = ensure_google_base_url(base_url)
    client = genai.Client(api_key=config["api_key"], http_options=http_options)
    return [name for m in client.models.list() if (name := _short_name(m.name))]


def _list_google_vertex(config: Mapping[str, str]) -> list[str]:
    from google import genai
    from google.oauth2 import service_account

    from lib.backends.gemini_shared import VERTEX_SCOPES

    credentials_path = config.get("credentials_path", "")
    if not credentials_path or not Path(credentials_path).is_file():
        raise BuiltinDiscoveryConfigError("file_not_found", path=credentials_path)
    with open(credentials_path, encoding="utf-8") as f:
        project_id = json.load(f).get("project_id")
    if not project_id:
        raise BuiltinDiscoveryConfigError("vertex_json_missing_project_id")
    credentials = service_account.Credentials.from_service_account_file(credentials_path, scopes=VERTEX_SCOPES)
    client = genai.Client(
        vertexai=True,
        project=project_id,
        location="global",
        credentials=credentials,
        http_options={"timeout": _GENAI_LIST_TIMEOUT_MS},
    )
    return [name for m in client.models.list() if (name := _short_name(m.name))]
