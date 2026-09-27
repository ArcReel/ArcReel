"""存量 Vertex 凭证经配置解析与后端装配后仍可用于生成。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.backends.backend_assembly.assembler import assemble_backend
from lib.config.resolver import ConfigResolver
from lib.db.repositories.credential_repository import CredentialRepository


@pytest.mark.parametrize("media_type", ["image", "video", "text"])
async def test_vertex_uses_recorded_credential_when_derived_file_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    db_factory: async_sessionmaker[AsyncSession],
    media_type: str,
) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    recorded = tmp_path / "legacy" / "vertex.json"
    recorded.parent.mkdir()
    recorded.write_text(json.dumps({"project_id": "legacy-project"}), encoding="utf-8")
    repo = CredentialRepository(db_session)
    cred = await repo.create(provider="gemini-vertex", name="Vertex", credentials_path=str(recorded))
    await repo.activate(cred.id, "gemini-vertex")
    await db_session.commit()

    with (
        patch("google.genai.Client"),
        patch("google.oauth2.service_account.Credentials.from_service_account_file") as load_credentials,
    ):
        backend = await assemble_backend(
            provider_id="gemini-vertex", media_type=media_type, model_id=None, resolver=ConfigResolver(db_factory)
        )

    assert backend is not None
    assert load_credentials.call_args.args[0] == str(recorded)
