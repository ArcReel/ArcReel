"""存量 Vertex 凭证经配置解析与后端装配后仍可用于生成。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.backends.backend_assembly.assembler import load_builtin_config
from lib.backends.backend_assembly.specs import builtin_model_spec
from lib.catalog import query as catalog
from lib.config.resolver import ConfigResolver
from lib.db.repositories.credential_repository import CredentialRepository
from lib.video_runtime.builtin import build_builtin_backend
from tests.fakes import captured_vertex_credentials, patched_genai_client


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

    model_id = catalog.default_model("gemini-vertex", media_type)
    spec = builtin_model_spec("gemini-vertex", media_type, model_id)
    config = await load_builtin_config(ConfigResolver(db_factory), "gemini-vertex", None)
    with (
        patched_genai_client(),
        captured_vertex_credentials() as load_credentials,
    ):
        backend = build_builtin_backend(spec, config, model_id)

    assert backend is not None
    assert load_credentials.call_args.args[0] == str(recorded)
