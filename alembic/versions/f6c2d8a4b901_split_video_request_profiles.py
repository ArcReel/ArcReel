"""Grok、Vidu、可灵按请求契约拆端点，冻结升级前名字识别规则回填。

只改旧端点；显式选过其他端点的模型保持不变。条目绑定优先，再按名字对应条目；
用户条目副本的官方名与别名参与旧规则。无条目时用 API 模型名，不把 API 名附加到条目名字后。
出厂快照的全部视频条目名字冻结在本迁移，不依赖以后刷新的快照；已同步的数据和用户副本按升级前顺序覆盖。
旧任务检查点不改写，由 RETIRED_ENDPOINT_SUCCESSORS 放行等价轮询；降级后新检查点无法在旧版本续跑。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6c2d8a4b901"
down_revision: str | Sequence[str] | None = "f6a2d8c4b901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIDU_PROFILES = {
    "viduq3-pro": "q3-pro",
    "viduq3-turbo": "q3-turbo",
    "viduq3-pro-fast": "q3-fast",
    "viduq3": "q3-reference",
    "viduq3-mix": "q3-reference",
    "viduq2": "q2",
    "viduq2-pro": "q2-pro",
    "viduq2-pro-fast": "q2-fast",
    "viduq2-turbo": "q2-fast",
    "viduq1": "q1",
    "viduq1-classic": "q1-classic",
    "vidu2.0": "2",
}
_KLING_PROFILES = {
    "kling-v2-5-turbo": "pro-silent",
    "kling-v2-6": "pro-audio",
    "kling-v3": "audio",
    "kling-v3-omni": "audio",
    "kling-video-o1": "silent",
}

_FACTORY_VERSION = "2026-11-03T12:42:27Z"
_OFFICIAL_NAMES = {
    "agnes/agnes-video-2.5": ("agnes-video-2.5",),
    "agnes/agnes-video-2.5-flash": ("agnes-video-2.5-flash",),
    "agnes/agnes-video-v2.0": ("agnes-video-v2.0",),
    "alibaba/happyhorse-1.0-i2v": ("happyhorse-1.0-i2v",),
    "alibaba/happyhorse-1.0-r2v": ("happyhorse-1.0-r2v",),
    "alibaba/happyhorse-1.0-t2v": ("happyhorse-1.0-t2v",),
    "alibaba/happyhorse-1.1-i2v": ("happyhorse-1.1-i2v",),
    "alibaba/happyhorse-1.1-r2v": ("happyhorse-1.1-r2v",),
    "alibaba/happyhorse-1.1-t2v": ("happyhorse-1.1-t2v",),
    "alibaba/wan2.7-i2v": ("wan2.7-i2v",),
    "alibaba/wan2.7-r2v": ("wan2.7-r2v",),
    "alibaba/wan2.7-t2v": ("wan2.7-t2v",),
    "alibaba/wan3.0-video": ("wan3.0-video",),
    "bytedance/doubao-seedance-1.5-pro": ("doubao-seedance-1.5-pro", "doubao-seedance-1-5-pro-251215"),
    "bytedance/doubao-seedance-2.0": ("doubao-seedance-2.0", "doubao-seedance-2-0-260128"),
    "bytedance/doubao-seedance-2.0-fast": ("doubao-seedance-2.0-fast", "doubao-seedance-2-0-fast-260128"),
    "bytedance/doubao-seedance-2.0-mini": ("doubao-seedance-2.0-mini", "doubao-seedance-2-0-mini-260615"),
    "bytedance/doubao-seedance-2.5": ("doubao-seedance-2.5", "doubao-seedance-2-5-260628"),
    "google/veo-3.1-fast-generate-001": ("veo-3.1-fast-generate-001",),
    "google/veo-3.1-fast-generate-preview": ("veo-3.1-fast-generate-preview",),
    "google/veo-3.1-generate-001": ("veo-3.1-generate-001",),
    "google/veo-3.1-generate-preview": ("veo-3.1-generate-preview",),
    "google/veo-3.1-lite-generate-preview": ("veo-3.1-lite-generate-preview",),
    "kuaishou/kling-v2-5-turbo": ("kling-v2-5-turbo",),
    "kuaishou/kling-v2-6": ("kling-v2-6",),
    "kuaishou/kling-v3": ("kling-v3",),
    "kuaishou/kling-v3-omni": ("kling-v3-omni",),
    "kuaishou/kling-video-o1": ("kling-video-o1",),
    "minimax/MiniMax-H3": ("MiniMax-H3",),
    "minimax/MiniMax-Hailuo-2.3": ("MiniMax-Hailuo-2.3",),
    "minimax/MiniMax-Hailuo-2.3-Fast": ("MiniMax-Hailuo-2.3-Fast",),
    "minimax/S2V-01": ("S2V-01",),
    "openai/sora-2": ("sora-2",),
    "openai/sora-2-pro": ("sora-2-pro",),
    "shengshu/vidu2.0": ("vidu2.0",),
    "shengshu/viduq3": ("viduq3",),
    "shengshu/viduq3-pro": ("viduq3-pro",),
    "shengshu/viduq3-turbo": ("viduq3-turbo",),
    "xai/grok-imagine-video": ("grok-imagine-video",),
    "xai/grok-imagine-video-1.5": ("grok-imagine-video-1.5",),
    "xai/grok-imagine-video-1.5-lite": ("grok-imagine-video-1.5-lite",),
}

_DEFAULTS = {"grok": "grok-video-mixed", "vidu": "vidu-video-generic", "kling": "kling-video-silent"}
_OLD = {"grok": "grok-video", "vidu": "vidu-video", "kling": "kling-video"}


def _endpoint(provider: str, names: tuple[str, ...]) -> str:
    for name in names:
        if provider == "grok" and name == "grok-imagine-video":
            return "grok-video-classic"
        if provider == "vidu" and name in _VIDU_PROFILES:
            return "vidu-video-" + _VIDU_PROFILES[name]
        key = name.replace(":", "/").rsplit("/", 1)[-1].strip().lower()
        if provider == "kling" and key in _KLING_PROFILES:
            return "kling-video-" + _KLING_PROFILES[key]
    return _DEFAULTS[provider]


def _tables() -> tuple[sa.TableClause, sa.TableClause]:
    def columns():
        return (
            sa.column("id", sa.Integer),
            sa.column("model_id", sa.String),
            sa.column("endpoint", sa.String),
            sa.column("entry_binding", sa.String),
        )

    return (
        sa.table("custom_provider_model", *columns()),
        sa.table(
            "builtin_provider_model",
            *columns(),
            sa.column("provider_id", sa.String),
            sa.column("user_added", sa.Boolean),
        ),
    )


def upgrade() -> None:
    conn = op.get_bind()
    users = sa.table(
        "catalog_user_entry",
        sa.column("maker", sa.String),
        sa.column("name", sa.String),
        sa.column("media_type", sa.String),
        sa.column("entry", sa.JSON),
    )
    entries = dict(_OFFICIAL_NAMES)
    synced = sa.table(
        "catalog_official_sync", sa.column("dataset_version", sa.String), sa.column("catalog", sa.LargeBinary)
    )
    saved = conn.execute(sa.select(synced.c.dataset_version, synced.c.catalog)).first()
    if saved is not None and saved.catalog:
        data = json.loads(saved.catalog)
        names = {
            entry["maker"] + "/" + entry["name"]: (entry["name"], *entry.get("aliases", ()))
            for entry in data["entries"]
            if entry["media_type"] == "video"
        }
        if saved.dataset_version and saved.dataset_version > _FACTORY_VERSION:
            entries = names | {key: value for key, value in entries.items() if key not in names}
        else:
            entries.update({key: value for key, value in names.items() if key not in entries})
    entries.update(
        {
            f"{maker}/{name}": (str(data.get("name", name)), *data.get("aliases", ()))
            for maker, name, data in conn.execute(
                sa.select(users.c.maker, users.c.name, users.c.entry).where(users.c.media_type == "video")
            )
        }
    )

    # 升级前按名字对应条目的归一化规则（ADR 0105）。
    def normalize(value: str) -> str:
        return value.rpartition("/")[2].lower().replace(".", "-")

    for table in _tables():
        for row in conn.execute(sa.select(table)).mappings().all():
            provider = next((p for p, old in _OLD.items() if row["endpoint"] == old), None)
            if provider is None or ("provider_id" in row and (row["provider_id"] != provider or not row["user_added"])):
                continue
            binding, model = row["entry_binding"], row["model_id"]
            names = entries.get(binding) if binding else None
            if names is None:
                hits = []
                for candidate in entries.values():
                    name, *aliases = candidate
                    rank = (
                        0
                        if model == name
                        else 1
                        if model in aliases
                        else 2
                        if normalize(model) == normalize(name)
                        else 3
                        if any(normalize(model) == normalize(alias) for alias in aliases)
                        else None
                    )
                    if rank is not None:
                        hits.append((rank, candidate))
                names = min(hits, key=lambda hit: hit[0])[1] if hits else (model,)
            conn.execute(sa.update(table).where(table.c.id == row["id"]).values(endpoint=_endpoint(provider, names)))


def downgrade() -> None:
    conn = op.get_bind()
    successors = {
        "grok": ("grok-video-classic", "grok-video-mixed"),
        "vidu": tuple("vidu-video-" + profile for profile in ("generic", *_VIDU_PROFILES.values())),
        "kling": tuple("kling-video-" + profile for profile in ("silent", *_KLING_PROFILES.values())),
    }
    for table in _tables():
        for provider, endpoints in successors.items():
            condition = table.c.endpoint.in_(endpoints)
            if "provider_id" in table.c:
                condition = sa.and_(condition, table.c.provider_id == provider, table.c.user_added.is_(True))
            conn.execute(sa.update(table).where(condition).values(endpoint=_OLD[provider]))
