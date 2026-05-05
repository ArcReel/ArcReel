"""backfill custom_model supported_durations

按 model_id 启发式填充 video endpoint 模型的 NULL supported_durations。
PRESETS 在迁移内 inline 复制（不 import lib.custom_provider.duration_presets），
让历史迁移与未来代码改动解耦。

Revision ID: 4c643f3ff5b9
Revises: 5b87accc10dd
Create Date: 2026-05-04 01:18:25.316138

"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c643f3ff5b9"
down_revision: str | Sequence[str] | None = "5b87accc10dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- inline 快照（与 lib/custom_provider/duration_presets.py 同步，但解耦演进）---
_DEFAULT_FALLBACK: list[int] = [4, 8]

_PRESETS: list[tuple[re.Pattern[str], list[int]]] = [
    (re.compile(r"^sora-2(-pro)?(-\d{4}-\d{2}-\d{2})?$", re.I), [4, 8, 12]),
    (re.compile(r"sora.*pro", re.I), [6, 10, 12, 16, 20]),
    (re.compile(r"veo-?\d", re.I), [4, 6, 8]),
    (re.compile(r"kling[-.]?(o1|v?[123](\.\d+)?)", re.I), [5, 10]),
    (re.compile(r"^(runway[-.]?)?gen-?\d", re.I), [5, 8, 10]),
    (re.compile(r"\bray-?\d", re.I), [5, 10]),
    (re.compile(r"dreamina|seedance", re.I), list(range(4, 16))),
    (re.compile(r"jimeng", re.I), list(range(4, 16))),
    (re.compile(r"happyhorse", re.I), list(range(3, 16))),
    (re.compile(r"grok[-.]?imagine", re.I), list(range(1, 16))),
    (re.compile(r"vidu", re.I), list(range(1, 17))),
    (re.compile(r"pixverse|^v[56](\.\d+)?$", re.I), list(range(1, 16))),
    (re.compile(r"hailuo|minimax", re.I), [6]),
    (re.compile(r"wan-?\d", re.I), [4, 5]),
    (re.compile(r"pika", re.I), [3, 5, 10]),
]

# 视频类 endpoint key 集合（与 lib/custom_provider/endpoints.py ENDPOINT_REGISTRY 同步快照）
_VIDEO_ENDPOINTS = ("openai-video", "newapi-video")


def _infer(model_id: str) -> list[int]:
    for pattern, durations in _PRESETS:
        if pattern.search(model_id):
            return list(durations)
    return list(_DEFAULT_FALLBACK)


def upgrade() -> None:
    bind = op.get_bind()
    placeholders = ",".join(f"'{ep}'" for ep in _VIDEO_ENDPOINTS)
    rows = bind.execute(
        sa.text(
            f"SELECT id, model_id FROM custom_provider_model "
            f"WHERE supported_durations IS NULL AND endpoint IN ({placeholders})"
        )
    ).fetchall()
    for row_id, model_id in rows:
        durations = _infer(model_id or "")
        bind.execute(
            sa.text("UPDATE custom_provider_model SET supported_durations = :v WHERE id = :id"),
            {"v": json.dumps(durations), "id": row_id},
        )


def downgrade() -> None:
    # 不主动清除（回填后保留即可，避免破坏数据）
    pass
