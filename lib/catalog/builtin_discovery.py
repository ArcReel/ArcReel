"""内置供应商的「获取模型」：调用供应商的模型列表接口，只取模型 ID，按清单中的 API 模型名精确匹配（docs/adr/0105）。

7 家内置供应商有可用的列表接口：gemini-aistudio、gemini-vertex 用 google 形态，openai、grok、dashscope、minimax、
agnes 用 OpenAI 兼容形态。列表接口返回的其他元信息不读取；获取结果也不用来判断模型是否可用，它会因账号、地域而不同。
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

#: 有模型列表接口的内置供应商与接口形态。
BUILTIN_DISCOVERY_FORMS: Mapping[str, DiscoveryForm] = {
    "gemini-aistudio": "google",
    "gemini-vertex": "google",
    "openai": "openai",
    "grok": "openai",
    "dashscope": "openai",
    "minimax": "openai",
    "agnes": "openai",
}

#: xAI 的 OpenAI 兼容接口地址；grok 的调用通道走 xai_sdk，不在配置里存地址。
XAI_OPENAI_BASE_URL = "https://api.x.ai/v1"


class BuiltinDiscoveryConfigError(ValueError):
    """凭证缺少获取模型需要的字段（如 Vertex 凭证文件不存在或没有 project_id）。``code`` 是错误文案的 i18n key。"""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass(frozen=True)
class DiscoveredBuiltinModel:
    """获取到的一个模型。

    ``recognized`` 为真时对上了清单里的官方收录模型，``model_key`` 是它的模型键（两栖模型的 API 模型名对上两个
    模型键，各列一行）；对不上时 ``model_key`` 为 ``None``，``media_type`` 先看名字对应上的条目，再按名字推断，
    添加时作为用户添加的模型的媒体类型；``entry`` 是名字对应上的条目（对应不上为 ``None``），添加后调用按它计价。
    """

    model_id: str
    media_type: str
    recognized: bool
    model_key: str | None
    entry: CatalogEntry | None


def is_discoverable(provider_id: str) -> bool:
    return provider_id in BUILTIN_DISCOVERY_FORMS


def match_by_name(provider_id: str, model_id: str) -> tuple[str, CatalogEntry | None]:
    """对不上官方收录模型时，按名字对应的条目与媒体类型（docs/adr/0105）。

    名字直接对应上条目时取条目的媒体类型；两栖模型的名字对应不上单个条目，先按名字推断媒体类型，再在该媒体类型内
    查一次。与添加后用户添加的模型经 ``catalog.find_entry`` 对应条目的口径一致，所以这里对应到的条目就是添加后
    计价所依据的条目。
    """
    entry = catalog.find_entry(model_id)
    if entry is not None:
        return entry.media_type, entry
    media_type = infer_media_type(model_id, BUILTIN_DISCOVERY_FORMS.get(provider_id, "openai"))
    return media_type, catalog.find_entry(model_id, media_type=media_type)


def name_media_type(provider_id: str, model_id: str) -> str:
    """对不上官方收录模型时的媒体类型：名字对应上条目取条目的，否则按名字推断（规则同自定义供应商的模型发现）。"""
    return match_by_name(provider_id, model_id)[0]


def classify_model_ids(provider_id: str, model_ids: list[str]) -> list[DiscoveredBuiltinModel]:
    """按清单中的 API 模型名精确匹配；结果按模型 ID 排序、去重。"""
    official: dict[str, list[catalog.ProviderModelView]] = {}
    for view in catalog.provider_models(provider_id):
        if view.official:
            official.setdefault(view.api_model_name, []).append(view)
    result: list[DiscoveredBuiltinModel] = []
    # 含点段的 ID 添加会被拒（见 model_id.py），不放进可添加列表
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
    """调用内置供应商的模型列表接口，返回模型 ID；同步执行网络请求，调用方放进线程。

    ``config`` 是叠加了凭证的供应商配置（``api_key``、``base_url``、``credentials_path``）。

    Raises:
        BuiltinDiscoveryConfigError: 凭证缺少获取模型需要的字段。
        ValueError: ``provider_id`` 没有模型列表接口。
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
    """列表接口的地址；每家都返回显式地址。传 ``None`` 时 OpenAI SDK 改读 ``OPENAI_BASE_URL`` 环境变量，会把密钥发到那里。"""
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


#: 模型列表接口单次请求的超时。调用方另有总时限，这里让线程随之结束，不按 SDK 默认的长超时与重试挂着。
_LIST_TIMEOUT_SECONDS = 30
#: 同一超时按 genai SDK ``HttpOptions.timeout`` 的单位（毫秒）；不设 ``retry_options`` 时它不重试。
_GENAI_LIST_TIMEOUT_MS = _LIST_TIMEOUT_SECONDS * 1000


def _list_openai(api_key: str, base_url: str) -> list[str]:
    from openai import OpenAI

    with OpenAI(api_key=api_key, base_url=base_url, timeout=_LIST_TIMEOUT_SECONDS, max_retries=0) as client:
        return [m.id for m in client.models.list() if m.id]


def _short_name(name: str | None) -> str:
    """去掉 ``models/``、``publishers/google/models/`` 这类路径前缀。"""
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
