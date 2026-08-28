"""split minimax video endpoints

Revision ID: 8c2b1e7d4a90
Revises: 3b7c921d5e44
Create Date: 2026-08-28 20:35:00.000000
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8c2b1e7d4a90"
down_revision: str | Sequence[str] | None = "3b7c921d5e44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_ENDPOINT = "minimax-video"
_NEW_ENDPOINTS = ("minimax-hailuo-v1", "minimax-hailuo-v1-fast", "minimax-s2v-01", "minimax-h3")


def _endpoint_for_model(model_id: object) -> str:
    """模型名 → 新键；与迁移落地时的 `_minimax_video_endpoint` / `infer_endpoint` 同口径。

    迁移是时点快照，字面量在此各存一份而不 import 应用代码：日后路由改口径也不该改写
    已迁移过的历史行。
    """
    model = str(model_id or "")
    lowered = model.lower()
    if model == "S2V-01":
        return "minimax-s2v-01"
    if "minimax-h3" in lowered:
        return "minimax-h3"
    if "hailuo" in lowered and "fast" in lowered:
        return "minimax-hailuo-v1-fast"
    return "minimax-hailuo-v1"


def _checkpoint_digest(checkpoint: Mapping[str, object]) -> str:
    payload = {key: value for key, value in checkpoint.items() if key not in {"api_call_id", "request_digest"}}
    if checkpoint.get("schema_version") == 2:
        payload.pop("artifact_visual_basis", None)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _rewrite_checkpoints(*, upgrading: bool) -> None:
    connection = op.get_bind()
    tasks = sa.table(
        "tasks",
        sa.column("task_id", sa.String()),
        sa.column("execution_checkpoint_json", sa.Text()),
    )
    rows = connection.execute(
        sa.select(tasks.c.task_id, tasks.c.execution_checkpoint_json).where(
            tasks.c.execution_checkpoint_json.is_not(None)
        )
    )
    for task_id, raw in rows:
        checkpoint = json.loads(raw)
        if not isinstance(checkpoint, dict):
            continue
        endpoint = checkpoint.get("endpoint_guard")
        if upgrading:
            if endpoint != _OLD_ENDPOINT:
                continue
            checkpoint["endpoint_guard"] = _endpoint_for_model(checkpoint.get("backend_model_id"))
        else:
            if endpoint not in _NEW_ENDPOINTS:
                continue
            checkpoint["endpoint_guard"] = _OLD_ENDPOINT
        checkpoint["request_digest"] = _checkpoint_digest(checkpoint)
        connection.execute(
            tasks.update()
            .where(tasks.c.task_id == task_id)
            .values(
                execution_checkpoint_json=json.dumps(
                    checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            )
        )


def upgrade() -> None:
    connection = op.get_bind()
    models = sa.table(
        "custom_provider_model",
        sa.column("id", sa.Integer()),
        sa.column("model_id", sa.String()),
        sa.column("endpoint", sa.String()),
    )
    rows = connection.execute(sa.select(models.c.id, models.c.model_id).where(models.c.endpoint == _OLD_ENDPOINT))
    for model_id_pk, model_id in rows:
        connection.execute(
            models.update().where(models.c.id == model_id_pk).values(endpoint=_endpoint_for_model(model_id))
        )
    _rewrite_checkpoints(upgrading=True)


def downgrade() -> None:
    connection = op.get_bind()
    models = sa.table(
        "custom_provider_model",
        sa.column("endpoint", sa.String()),
    )
    connection.execute(models.update().where(models.c.endpoint.in_(_NEW_ENDPOINTS)).values(endpoint=_OLD_ENDPOINT))
    _rewrite_checkpoints(upgrading=False)
