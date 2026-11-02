"""生成时为参考图选用供应商素材关联：按调用通道实际使用的推理凭证与它的绑定判断。

每张参考图按实际发出的字节对应到内容版本，再在本次调用的素材库范围里查选定的关联：

- 调用通道对参考图槽位没有声明素材引用、推理凭证没有已确认的绑定、图片不是可上传的资产图或衍生图、
  发出的字节不是当前内容版本，或该内容版本在这个范围里没有选定的关联：没有适用关联，照常发图片内容；
- 选定的关联可用：以 ``asset://`` 素材引用代替这张图片的内容，位置与提示词里的图片编号不变；
- 选定的关联或入库任务尚在处理、结果待确认或已失败：适用但不可用，明确失败，不改发图片内容。

只读：不为早期版本记录补写身份，没有身份的记录不可能有关联。关联的查询与状态机见 :mod:`lib.asset_library.links`。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.artifacts.version_manager import VersionManager
from lib.asset_library.bindings import AssetLibraryScope, resolve_bound_scope
from lib.asset_library.links import UPLOADABLE_RESOURCE_TYPES, asset_uri, is_usable, selected_link
from lib.backends.provider_assets import REFERENCE_IMAGE_ROLE, inference_credential, provider_asset_roles
from lib.db.models.asset_library import ProviderAssetLink
from lib.material.storage import CONTENT_DIGEST, CONTENT_VERSION_ID
from lib.project.resource_paths import resource_of_path


@dataclass(frozen=True)
class ReferenceImageSource:
    """一张将要发出的参考图：来源文件的项目内相对路径、发出字节的摘要与资产（或衍生）名称。"""

    locator: str
    sha256: str
    name: str


@dataclass(frozen=True)
class ReferenceAssetChoice:
    """以素材引用发出的一张参考图：它对应的内容版本与选定关联。"""

    asset_uri: str
    content_version_id: str
    link_id: int


class ProviderAssetLinkUnavailableError(RuntimeError):
    """参考图在本次调用范围里有选定的关联或入库任务，但它此刻不能用于生成。

    ``code`` 与 ``params`` 按任务失败的结构化编码落库：处理中提示等待，失败给出供应商原因并提示解除关联。
    """

    def __init__(self, link: ProviderAssetLink, *, index: int, name: str) -> None:
        self.link_id = link.id
        self.state = link.state
        params: dict[str, Any] = {"index": index, "name": name}
        if link.state == "failed":
            self.code = "provider_asset_link_failed"
            params["error_code"] = link.error_code or "-"
            if link.error_message:
                params["provider_reason"] = link.error_message
        else:
            self.code = "provider_asset_link_processing"
        self.params = params
        super().__init__(f"{self.code}: image {index} ({name}) link {link.id} is {link.state}")


async def call_scope(session: AsyncSession, backend: object) -> AssetLibraryScope | None:
    """调用通道本次可用的素材库范围；通道不接受参考图素材引用或凭证没有已确认的绑定时为 None。"""
    if REFERENCE_IMAGE_ROLE not in provider_asset_roles(backend):
        return None
    credential = inference_credential(backend)
    if credential is None:
        return None
    return await resolve_bound_scope(
        session,
        credential_type=credential.credential_type,
        credential_id=credential.credential_id,
        api_key=credential.api_key,
        base_url=credential.base_url,
        default_base_url=credential.default_base_url,
    )


def _content_version_ids(project_path: Path, sources: Sequence[ReferenceImageSource]) -> list[str | None]:
    """每张参考图发出的字节对应的内容版本身份：须是资源当前选中的版本，摘要与发出的字节一致。"""
    manager = VersionManager(project_path)
    ids: list[str | None] = []
    for source in sources:
        resource = resource_of_path(source.locator)
        if resource is None or resource[0] not in UPLOADABLE_RESOURCE_TYPES:
            ids.append(None)
            continue
        info = manager.get_versions(*resource)
        current = info.get("current_version", 0)
        record = next((v for v in info.get("versions", []) if v.get("version") == current), None)
        if record is None or record.get(CONTENT_DIGEST) != source.sha256:
            ids.append(None)
            continue
        content_version_id = record.get(CONTENT_VERSION_ID)
        ids.append(content_version_id if isinstance(content_version_id, str) and content_version_id else None)
    return ids


async def choose_reference_image_assets(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    backend: object,
    project_name: str,
    project_path: Path,
    sources: Sequence[ReferenceImageSource],
) -> tuple[ReferenceAssetChoice | None, ...]:
    """与 ``sources`` 等长同序的选择：可用关联给出素材引用，没有适用关联为 None。

    Raises:
        ProviderAssetLinkUnavailableError: 某张参考图的适用关联尚未可用或入库失败；按图片顺序报第一张。
    """
    none: tuple[ReferenceAssetChoice | None, ...] = (None,) * len(sources)
    if not sources:
        return none
    async with session_factory() as session:
        scope = await call_scope(session, backend)
        if scope is None:
            return none
        content_version_ids = await asyncio.to_thread(_content_version_ids, project_path, sources)
        choices: list[ReferenceAssetChoice | None] = []
        for position, (source, content_version_id) in enumerate(zip(sources, content_version_ids, strict=True)):
            if content_version_id is None:
                choices.append(None)
                continue
            link = await selected_link(
                session, project_name=project_name, content_version_id=content_version_id, scope=scope
            )
            if link is None:
                choices.append(None)
                continue
            uri = asset_uri(link)
            if not is_usable(link) or uri is None:
                raise ProviderAssetLinkUnavailableError(link, index=position + 1, name=source.name)
            choices.append(ReferenceAssetChoice(asset_uri=uri, content_version_id=content_version_id, link_id=link.id))
    return tuple(choices)


__all__ = [
    "ProviderAssetLinkUnavailableError",
    "ReferenceAssetChoice",
    "ReferenceImageSource",
    "call_scope",
    "choose_reference_image_assets",
]
