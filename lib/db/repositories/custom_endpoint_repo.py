"""Custom invocation-endpoint repository: definition CRUD and model-reference queries."""

from __future__ import annotations

from typing import Any, NamedTuple

from sqlalchemy import delete, select

from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.models.market_installation import MarketInstallation
from lib.db.repositories.base import BaseRepository


class EndpointReference(NamedTuple):
    """A model row referencing this invocation endpoint, together with its provider."""

    provider_id: int
    provider_display_name: str
    model_id: str
    model_display_name: str


class EndpointAttachment(NamedTuple):
    """A model row referencing this invocation endpoint, with its provider's declared model-discovery protocol.

    Separate from :class:`EndpointReference`: that is a user-facing reference list; this
    supplies facts for endpoint/provider-protocol pairing. Adding a protocol field there
    would change the public 409 response shape.
    """

    provider_id: int
    model_id: str
    discovery_format: str


class CustomEndpointRepository(BaseRepository):
    """Read/write access to the ``custom_endpoint`` table.

    Callers derive mirror columns (``kind`` / ``schema_version`` / ``media_type`` /
    ``display_name``) from ``definition`` before writing and pass them together. This layer
    does not parse definitions: format-level derivation belongs to
    ``lib.custom_provider.endpoint_resolution``; the repository only persists values.
    """

    async def create(
        self,
        *,
        definition: dict[str, Any],
        kind: str,
        schema_version: str,
        media_type: str,
        display_name: str,
    ) -> CustomEndpoint:
        endpoint = CustomEndpoint(
            definition=definition,
            kind=kind,
            schema_version=schema_version,
            media_type=media_type,
            display_name=display_name,
        )
        self.session.add(endpoint)
        await self.session.flush()  # Retrieve the auto-incremented id; endpoint key ce-<id> derives from it
        return endpoint

    async def get(self, endpoint_id: int) -> CustomEndpoint | None:
        stmt = select(CustomEndpoint).where(CustomEndpoint.id == endpoint_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> list[CustomEndpoint]:
        stmt = select(CustomEndpoint).order_by(CustomEndpoint.id)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def update(
        self,
        endpoint_id: int,
        *,
        definition: dict[str, Any],
        kind: str,
        schema_version: str,
        media_type: str,
        display_name: str,
    ) -> CustomEndpoint | None:
        """Replace the whole definition and mirror columns in place, retaining id (the endpoint key) and model attachments; return None when absent."""
        endpoint = await self.get(endpoint_id)
        if endpoint is None:
            return None
        endpoint.definition = definition
        endpoint.kind = kind
        endpoint.schema_version = schema_version
        endpoint.media_type = media_type
        endpoint.display_name = display_name
        await self.session.flush()
        return endpoint

    async def delete(self, endpoint_id: int) -> None:
        """Delete together with installation records.

        Explicitly delete installation records rather than relying on FK CASCADE, which
        requires foreign_keys pragma; leftover records prevent reinstalling the same market entry.
        """
        await self.session.execute(
            delete(MarketInstallation).where(MarketInstallation.custom_endpoint_id == endpoint_id)
        )
        await self.session.execute(delete(CustomEndpoint).where(CustomEndpoint.id == endpoint_id))
        await self.session.flush()

    # ── Referential integrity ────────────────────────────────────────────────

    async def list_attachments(self, endpoint_key: str) -> list[EndpointAttachment]:
        """List referencing model rows and provider protocols by literal ``endpoint`` key.

        When replacing the whole definition, use these facts to check whether the new
        container kind remains attachable. Pairing rules belong to
        ``lib.custom_provider.discovery_formats``; this layer only retrieves facts.
        """
        stmt = (
            select(
                CustomProvider.id,
                CustomProviderModel.model_id,
                CustomProvider.discovery_format,
            )
            .join(CustomProvider, CustomProvider.id == CustomProviderModel.provider_id)
            .where(CustomProviderModel.endpoint == endpoint_key)
            .order_by(CustomProviderModel.provider_id, CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        return [EndpointAttachment(*row) for row in result.all()]

    async def list_references(self, endpoint_key: str) -> list[EndpointReference]:
        """List referencing model rows by literal ``endpoint`` key so 409 responses identify remaining users.

        This query, not a foreign key, enforces referential integrity: 409 must return a
        reference list, while FK constraints only raise IntegrityError. Deletion already
        must query references first (docs/adr/0067).
        """
        stmt = (
            select(
                CustomProvider.id,
                CustomProvider.display_name,
                CustomProviderModel.model_id,
                CustomProviderModel.display_name,
            )
            .join(CustomProvider, CustomProvider.id == CustomProviderModel.provider_id)
            .where(CustomProviderModel.endpoint == endpoint_key)
            .order_by(CustomProviderModel.provider_id, CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        return [EndpointReference(*row) for row in result.all()]
