"""split task interface identity, request URL, and artifact collection

Revision ID: c8e1d4a91b70
Revises: f6a41746c0de
Create Date: 2026-08-14 08:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from lib.db.migration_helpers import preserve_sqlite_indexes

revision: str = "c8e1d4a91b70"
down_revision: str | Sequence[str] | None = "f6a41746c0de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TASK_TYPE_COLLECTION = {
    "storyboard": "storyboards",
    "video": "videos",
    "reference_video": "reference_videos",
    "tts": "audio",
    "grid": "grids",
    "grid_split": "grids",
    "voice_sample": "audio",
    "character": "characters",
    "scene": "scenes",
    "prop": "props",
    "product": "products",
    "end_frame": "end_frames",
    "image_edit": None,
}

# image_edit 按目标资产种类定产物集合。两张表都是 lib.resource_paths 映射在本 revision 的冻结快照：
# 迁移只对既有行做一次性改写，语义须锁在写入时的口径，不随后续 lib 改动漂移。
_TARGET_TYPE_COLLECTION = {
    "character": "characters",
    "scene": "scenes",
    "prop": "props",
    "product": "products",
    "storyboard": "storyboards",
    "video": "videos",
}


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("artifact_collection", sa.String(), nullable=True))

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT task_id, task_type, resource_type, provider_endpoint, submitted_base_url FROM tasks")
    )
    for task_id, task_type, resource_type, provider_endpoint, submitted_base_url in rows:
        collection = _TASK_TYPE_COLLECTION.get(task_type)
        if task_type == "image_edit":
            collection = _TARGET_TYPE_COLLECTION.get(resource_type or "", resource_type)
        updates: dict[str, object] = {}
        if collection:
            updates["artifact_collection"] = collection
        endpoint = provider_endpoint if isinstance(provider_endpoint, str) else ""
        if endpoint.lower().startswith(("http://", "https://")) and not submitted_base_url:
            updates["submitted_base_url"] = provider_endpoint
            updates["provider_endpoint"] = None
        if updates:
            conn.execute(
                sa.text(
                    "UPDATE tasks SET " + ", ".join(f"{key} = :{key}" for key in updates) + " WHERE task_id = :task_id"
                ),
                {"task_id": task_id, **updates},
            )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT task_id, provider_endpoint, submitted_base_url FROM tasks"))
    for task_id, provider_endpoint, submitted_base_url in rows:
        url = submitted_base_url if isinstance(submitted_base_url, str) else ""
        if url.lower().startswith(("http://", "https://")) and not provider_endpoint:
            conn.execute(
                sa.text(
                    "UPDATE tasks SET provider_endpoint = :url, submitted_base_url = NULL WHERE task_id = :task_id"
                ),
                {"url": submitted_base_url, "task_id": task_id},
            )
    with preserve_sqlite_indexes("tasks"):
        with op.batch_alter_table("tasks", schema=None) as batch_op:
            batch_op.drop_column("artifact_collection")
