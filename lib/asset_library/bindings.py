"""素材库资源范围与推理凭证的显式绑定。

绑定只回答「这条推理凭证当前被用户确认可以使用哪个素材库范围」，不替代供应商自己的权限校验。
确认时记下推理凭证内容、endpoint 与素材库范围的摘要；读取时按当前值重新计算，任何一项变化都让
绑定退回待确认，不因凭证记录 ID 未变而继承旧账号资格。是否支持素材引用不按 base_url 的域名推断，
内置火山方舟凭证与任意自定义供应商都可以由用户显式绑定。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.registry import PROVIDER_REGISTRY
from lib.db.base import utc_now
from lib.db.models.asset_library import AssetLibraryAccount, AssetLibraryBinding
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.db.repositories.credential_repository import CredentialRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository

CredentialType = Literal["builtin", "custom"]

# 可绑定素材库的内置供应商：调用走火山方舟协议的那几家。
BINDABLE_BUILTIN_PROVIDERS: tuple[str, ...] = ("ark", "ark-agent-plan")

BindingStatus = Literal["confirmed", "needs_confirmation"]


@dataclass(frozen=True)
class AssetLibraryScope:
    """一次调用实际可用的素材库范围：账号记录、站点、区域与项目。"""

    account_id: int
    site: str
    region: str
    project_name: str


@dataclass(frozen=True)
class InferenceCredential:
    """可绑定的一条推理凭证，带计算摘要所需的实际内容；``api_key`` 只在进程内使用，不出 API。"""

    credential_type: CredentialType
    credential_id: int
    provider: str  # 内置供应商 id，或自定义供应商的 ``custom-<id>``
    name: str
    api_key: str
    endpoint: str


@dataclass(frozen=True)
class BindingView:
    credential: InferenceCredential
    account_id: int
    status: BindingStatus


def effective_endpoint(base_url: str | None, default: str | None = None) -> str:
    """调用实际发往的 endpoint：去掉空白与尾斜杠，空值回落到默认地址。"""
    value = (base_url or "").strip() or (default or "").strip()
    return value.rstrip("/")


def binding_fingerprint(credential: InferenceCredential, account: AssetLibraryAccount) -> str:
    """推理凭证内容、endpoint 与素材库范围的摘要；任一项变化都会得到不同的值。"""
    payload = json.dumps(
        {
            "credential_type": credential.credential_type,
            "credential_id": credential.credential_id,
            "api_key": credential.api_key.strip(),
            "endpoint": credential.endpoint,
            "site": account.site,
            "region": account.region,
            "project_name": account.project_name,
            "access_key_id": account.access_key_id.strip(),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def list_bindable_credentials(session: AsyncSession) -> list[InferenceCredential]:
    """全部可绑定的推理凭证：内置火山方舟系供应商的各条凭证，以及每个自定义供应商。"""
    credentials: list[InferenceCredential] = []
    cred_repo = CredentialRepository(session)
    for provider in BINDABLE_BUILTIN_PROVIDERS:
        default = PROVIDER_REGISTRY[provider].default_base_url
        for cred in await cred_repo.list_by_provider(provider):
            if not cred.api_key:
                continue
            credentials.append(
                InferenceCredential(
                    credential_type="builtin",
                    credential_id=cred.id,
                    provider=provider,
                    name=cred.name,
                    api_key=cred.api_key,
                    endpoint=effective_endpoint(cred.base_url, default),
                )
            )
    credentials.extend(
        InferenceCredential(
            credential_type="custom",
            credential_id=custom.id,
            provider=custom.provider_id,
            name=custom.display_name,
            api_key=custom.api_key or "",
            endpoint=effective_endpoint(custom.base_url),
        )
        for custom in await CustomProviderRepository(session).list_providers()
    )
    return credentials


async def get_bindable_credential(
    session: AsyncSession, credential_type: CredentialType, credential_id: int
) -> InferenceCredential | None:
    for credential in await list_bindable_credentials(session):
        if credential.credential_type == credential_type and credential.credential_id == credential_id:
            return credential
    return None


def binding_status(
    binding: AssetLibraryBinding, credential: InferenceCredential, account: AssetLibraryAccount
) -> BindingStatus:
    if binding.confirmed_fingerprint == binding_fingerprint(credential, account):
        return "confirmed"
    return "needs_confirmation"


async def list_binding_views(session: AsyncSession) -> list[BindingView]:
    """现存推理凭证上的绑定及其状态；指向已删除凭证或账号的绑定不列出。"""
    repo = AssetLibraryRepository(session)
    accounts = {account.id: account for account in await repo.list_accounts()}
    credentials: dict[tuple[str, int], InferenceCredential] = {
        (c.credential_type, c.credential_id): c for c in await list_bindable_credentials(session)
    }
    views: list[BindingView] = []
    for binding in await repo.list_bindings():
        credential = credentials.get((binding.credential_type, binding.credential_id))
        account = accounts.get(binding.account_id)
        if credential is None or account is None:
            continue
        views.append(
            BindingView(
                credential=credential, account_id=account.id, status=binding_status(binding, credential, account)
            )
        )
    return views


async def confirm_binding(
    session: AsyncSession, account: AssetLibraryAccount, credential: InferenceCredential
) -> AssetLibraryBinding:
    """按凭证与账号的当前内容确认绑定；凭证原先绑定其他账号时改绑。"""
    return await AssetLibraryRepository(session).upsert_binding(
        account_id=account.id,
        credential_type=credential.credential_type,
        credential_id=credential.credential_id,
        fingerprint=binding_fingerprint(credential, account),
        confirmed_at=utc_now(),
    )


async def resolve_bound_scope(
    session: AsyncSession,
    *,
    credential_type: CredentialType,
    credential_id: int,
    api_key: str,
    base_url: str | None,
    default_base_url: str | None = None,
) -> AssetLibraryScope | None:
    """一次调用实际使用的推理凭证所确认的素材库范围。

    ``api_key`` 与 ``base_url`` 是本次调用真正使用的值，而不是按记录 ID 重新读出的值：凭证在
    读取与调用之间被修改时，摘要对不上即按无绑定处理。没有绑定、绑定待确认或账号已删除都返回 None。
    """
    repo = AssetLibraryRepository(session)
    binding = await repo.get_binding_for_credential(credential_type, credential_id)
    if binding is None:
        return None
    account = await repo.get_account(binding.account_id)
    if account is None:
        return None
    actual = InferenceCredential(
        credential_type=credential_type,
        credential_id=credential_id,
        provider="",
        name="",
        api_key=api_key,
        endpoint=effective_endpoint(base_url, default_base_url),
    )
    if binding_status(binding, actual, account) != "confirmed":
        return None
    return AssetLibraryScope(
        account_id=account.id, site=account.site, region=account.region, project_name=account.project_name
    )
