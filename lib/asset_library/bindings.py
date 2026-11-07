"""Explicit binding between library resource scopes and inference credentials.

Bindings only record which scope users currently confirm usable by a credential; never replace
provider authorization. Confirmation digests credential contents, endpoint, and library scope.
Recompute from current values on reads; any change returns bindings to pending confirmation,
never inheriting old-account eligibility just because record IDs match. Never infer asset-
reference support from base_url domains; users may bind built-in Volcengine Ark credentials
or arbitrary custom providers explicitly.
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

# Built-in bindable providers use the Volcengine Ark protocol.
BINDABLE_BUILTIN_PROVIDERS: tuple[str, ...] = ("ark", "ark-agent-plan")

BindingStatus = Literal["confirmed", "needs_confirmation"]


#: Identity-digest version prefix; change when algorithms change so old links never equal new digests.
_ACCOUNT_IDENTITY_DOMAIN = "arcreel/asset-library-account/v1"


def account_identity(site: str, access_key_id: str) -> str:
    """Irreversible cloud-account resource-domain identity: site/Access Key ID digest, excluding Secret Access Key.

    Record IDs and site/region/project values can change in place and cannot distinguish switching
    the same record to another cloud account’s keys. Provider asset IDs belong only to their
    creating cloud account, so links/ingest tasks freeze this identity. Changed Access Key IDs
    mean another resource domain; old links retain original scopes and do not apply to new
    identities. Changing only Secret Access Keys leaves identity unchanged.
    """
    payload = json.dumps(
        {"domain": _ACCOUNT_IDENTITY_DOMAIN, "site": site, "access_key_id": access_key_id.strip()},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AssetLibraryScope:
    """Actual usable library scope: cloud-account identity, site, region, project, and supplying account record.

    Link applicability depends only on ``account_identity``, ``site``, ``region``, ``project_name``.
    ``account_id`` identifies control-plane access records only; changing records to another
    cloud account changes their identity.
    """

    account_id: int
    account_identity: str
    site: str
    region: str
    project_name: str


def account_scope(account: AssetLibraryAccount) -> AssetLibraryScope:
    """Library scope covered by an account record’s current contents."""
    return AssetLibraryScope(
        account_id=account.id,
        account_identity=account_identity(account.site, account.access_key_id),
        site=account.site,
        region=account.region,
        project_name=account.project_name,
    )


def scope_key(scope: AssetLibraryScope) -> str:
    """Opaque comparison key for UI scope equality; neither contains nor reveals Access Key IDs."""
    payload = json.dumps([scope.account_identity, scope.site, scope.region, scope.project_name], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class InferenceCredential:
    """Bindable inference credential with actual digest inputs; ``api_key`` stays process-local, never exposed through APIs."""

    credential_type: CredentialType
    credential_id: int
    provider: str  # Built-in provider ID or custom-provider ``custom-<id>``.
    name: str
    api_key: str
    endpoint: str


@dataclass(frozen=True)
class BindingView:
    credential: InferenceCredential
    account_id: int
    status: BindingStatus


def effective_endpoint(base_url: str | None, default: str | None = None) -> str:
    """Actual inference endpoint, trimmed with trailing slashes removed; empty values fall back to defaults."""
    value = (base_url or "").strip() or (default or "").strip()
    return value.rstrip("/")


def binding_fingerprint(credential: InferenceCredential, account: AssetLibraryAccount) -> str:
    """Digest of credential contents/endpoint/library scope; changes to any input change the result."""
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
    """All bindable inference credentials: every built-in Volcengine-Ark-family credential and each custom provider."""
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
    """Bindings/status for existing inference credentials; omit bindings targeting deleted credentials/accounts."""
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
    """Confirm against current credential/account contents, rebinding if the credential previously used another account."""
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
    """Confirmed library scope for inference credentials actually used in this call.

    ``api_key`` / ``base_url`` are actual invocation values, not values reread by record ID.
    Credentials changed between reading/calling produce digest mismatches and count as unbound.
    Missing/unconfirmed bindings or deleted accounts return None.
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
    return account_scope(account)
