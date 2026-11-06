"""按请求契约拆分 Agnes 与百炼视频端点，Vertex 音轨按凭证协议声明。

Revision ID: f6d2a8c4b931
Revises: f6c2d8a4b901

只改旧端点；绑定条目优先，用户副本的官方名和别名覆盖冻结的出厂条目。无绑定时按升级前的归一化名字匹配。
不改能力覆盖、条目或检查点。续跑守卫接受旧端点的后继；降级后新任务的守卫无法被旧代码认得。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision: str = "f6d2a8c4b931"
down_revision: str | Sequence[str] | None = "f6c2d8a4b901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SNAPSHOT_VERSION = "2026-11-03T12:42:27Z"

_ENTRY_NAMES = {
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


def _normalized(name: str) -> str:
    return name.rpartition("/")[2].lower().replace(".", "-")


def _names(model: str, binding: str | None, entries: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    if binding and binding in entries:
        return entries[binding]
    normalized = _normalized(model)
    for rank in range(4):
        for names in entries.values():
            if (
                (rank == 0 and model == names[0])
                or (rank == 1 and model in names[1:])
                or (rank == 2 and normalized == _normalized(names[0]))
                or (rank == 3 and any(normalized == _normalized(n) for n in names[1:]))
            ):
                return names
    return (model,)


def _endpoint(old: str, names: tuple[str, ...]) -> str:
    if old == "agnes-video":
        known = next((n for n in names if n in {"agnes-video-v2.0", "agnes-video-2.5", "agnes-video-2.5-flash"}), None)
        return "agnes-video-2-5" if known in {"agnes-video-2.5", "agnes-video-2.5-flash"} else "agnes-video-2"
    if old == "gemini-video":
        return "gemini-vertex-video"
    if any(re.search(r"(?<![a-z0-9])wan[-_]?3(?![a-z0-9])", n, re.I) for n in names):
        return "dashscope-wan3-video"
    for name in names:
        normalized = name.strip().lower()
        wan27 = re.search(r"(?<![a-z0-9])wan[-_]?2\.7(?![a-z0-9])", normalized)
        if wan27:
            suffix = normalized[wan27.start() :]
            if re.search(r"(?<![a-z0-9])(?:video[-_]?edit|[sv]2v)(?![a-z0-9])", suffix):
                continue
            suffix = re.sub(r"wan[-_]?2\.7[_-]", "wan2.7-", suffix)
            suffix = re.sub(r"(?<![a-z0-9])image[-_]?(?:to|2)[-_]?video(?![a-z0-9])", "i2v", suffix)
            normalized = normalized[: wan27.start()] + suffix
        if re.search(r"(?<![a-z0-9])wan2\.7-r2v(?![a-z0-9])", normalized):
            return "dashscope-reference-voice-video"
        if re.search(r"(?<![a-z0-9])happyhorse-1\.[01]-r2v(?![a-z0-9])", normalized):
            return "dashscope-reference-video"
        if re.search(r"(?<![a-z0-9])(?:wan2\.7|happyhorse-1\.[01])-(?:t2v|i2v)(?![a-z0-9])", normalized):
            return "dashscope-always-on-video"
    return "dashscope-video"


def _table(name: str) -> sa.TableClause:
    columns = [
        sa.column("id", sa.Integer),
        sa.column("model_id", sa.String),
        sa.column("endpoint", sa.String),
        sa.column("entry_binding", sa.String),
    ]
    if name == "builtin_provider_model":
        columns.append(sa.column("provider_id", sa.String))
    return sa.table(name, *columns)


def upgrade() -> None:
    connection = op.get_bind()
    entries = dict(_ENTRY_NAMES)
    raw_catalog = connection.execute(sa.text("SELECT catalog FROM catalog_official_sync WHERE id = 1")).scalar()
    if raw_catalog is not None:
        synced = json.loads(raw_catalog)
        synced_names = {
            f"{entry['maker']}/{entry['name']}": (entry["name"], *entry.get("aliases", []))
            for entry in synced.get("entries", [])
            if entry.get("media_type") == "video"
        }
        if datetime.fromisoformat(synced["dataset_version"]) > datetime.fromisoformat(_SNAPSHOT_VERSION):
            entries = synced_names | {key: names for key, names in entries.items() if key not in synced_names}
        else:
            entries.update({key: names for key, names in synced_names.items() if key not in entries})
    for maker, name, raw in connection.execute(
        sa.text("SELECT maker, name, entry FROM catalog_user_entry WHERE media_type = 'video'")
    ):
        entry = json.loads(raw) if isinstance(raw, str) else raw
        entries[f"{maker}/{name}"] = (entry["name"], *entry.get("aliases", []))
    for table_name in ("custom_provider_model", "builtin_provider_model"):
        table = _table(table_name)
        for row in connection.execute(sa.select(table)).mappings().all():
            old = row["endpoint"]
            provider = row.get("provider_id")
            if old not in ("agnes-video", "dashscope-async-video", "gemini-video"):
                continue
            if (
                provider is not None
                and provider
                not in {
                    "agnes-video": ("agnes",),
                    "dashscope-async-video": ("dashscope",),
                    "gemini-video": ("gemini-vertex",),
                }[old]
            ):
                continue
            # 自定义 Gemini 只用 AI Studio，协议没有拆分。
            if provider is None and old == "gemini-video":
                continue
            connection.execute(
                sa.update(table)
                .where(table.c.id == row["id"])
                .values(endpoint=_endpoint(old, _names(row["model_id"], row["entry_binding"], entries)))
            )


def downgrade() -> None:
    connection = op.get_bind()
    for table_name in ("custom_provider_model", "builtin_provider_model"):
        table = _table(table_name)
        for old, successors in {
            "agnes-video": ("agnes-video-2", "agnes-video-2-5"),
            "dashscope-async-video": (
                "dashscope-video",
                "dashscope-always-on-video",
                "dashscope-reference-video",
                "dashscope-reference-voice-video",
                "dashscope-wan3-video",
            ),
            "gemini-video": ("gemini-vertex-video",),
        }.items():
            connection.execute(sa.update(table).where(table.c.endpoint.in_(successors)).values(endpoint=old))
