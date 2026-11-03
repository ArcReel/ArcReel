"""用户条目副本补齐模型事实：参考图上限、视频输入方式与参考音频、图片 token 估算基数

Revision ID: b7f2a6d91c40
Revises: d5a9c3e71f28
Create Date: 2026-11-03 22:00:00.000000

模型条目新增了几项可选的模型事实（视频与图片的参考图上限，视频是否接受文生视频、首帧、尾帧和参考音频及参考音频
的数量与总时长上限，图片的 token 估算基数）。官方数据补录了这些字段，但用户条目副本不跟随官方同步，补不到它们；
实例改读条目之后，副本上的「未收录」会让模型失去升级前可用的能力或估算。条目未收录首帧、尾帧时实例只开首帧，
因此升级前接受尾帧的副本必须显式记下尾帧。

本迁移按升级前调用通道按名字认定的规则，给副本补上这些字段：拿来比对的名字是副本的官方模型名与模型别名，
与升级前按条目认定通道能力时用的名字相同；第一个认得的名字决定取值。规则在此冻结一份、不 import 应用代码：
这些规则会从调用通道里删去，跨多个版本直接升级时结果也要相同。取值是升级前代码的结果，不是官方新数据。

- 只补副本上没有的字段，用户填过的保持不变；参考音频的三项按一组处理，填过其中一项就整组不补。
- 认不得的名字升级前只有通道的统一回落，不是模型事实，保持未收录。图片参考图上限只补按型号名区分的几家，
  整条通道共用的常量（以及「0 = 不裁剪」）留给调用端点声明。
- 不改 ``user_created``、``updated_at``，有行即停止同步的状态不变。读不出的副本（不是对象或缺 ``facts``）跳过。
- 降级不删除补上的字段：旧版本读条目时忽略不认识的字段，补上的值与当时的行为一致。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7f2a6d91c40"
down_revision: str | Sequence[str] | None = "d5a9c3e71f28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUDIO_KEYS = ("reference_audio", "max_reference_audio_count", "max_reference_audio_total_seconds")

Facts = dict[str, object]


def _video(
    *,
    text_to_video: bool = True,
    first_frame: bool = True,
    last_frame: bool = False,
    max_reference_images: int = 0,
    audio: tuple[int, float | None] | None = None,
) -> Facts:
    """一组视频事实；``audio`` 是接受参考音频时的（段数上限，总时长上限），``None`` 表示不接受。"""
    facts: Facts = {
        "max_reference_images": max_reference_images,
        "text_to_video": text_to_video,
        "first_frame": first_frame,
        "last_frame": last_frame,
        "reference_audio": audio is not None,
    }
    if audio is not None:
        count, total_seconds = audio
        facts["max_reference_audio_count"] = count
        if total_seconds is not None:
            facts["max_reference_audio_total_seconds"] = total_seconds
    return facts


# ── 方舟 Seedance（lib/backends/video_backends/ark.py）─────────────────────

_SEEDANCE_KNOWN_SUFFIX = re.compile(r"^(-\d+)?$")
_SEEDANCE_2_5_VERIFIED = ("seedance-2-5", "seedance-2.5")
_SEEDANCE_2_VERIFIED = (
    "seedance-2-0-fast",
    "seedance-2.0-fast",
    "seedance-2-0-mini",
    "seedance-2.0-mini",
    "seedance-2-0",
    "seedance-2.0",
)
_SEEDANCE_1_NO_FIRST_FRAME = ("seedance-1-0-lite-t2v", "seedance-1.0-lite-t2v")
_SEEDANCE_1_LAST_FRAME = (
    "seedance-1-5-pro",
    "seedance-1.5-pro",
    "seedance-1-0-pro",
    "seedance-1.0-pro",
    "seedance-1-0-lite-i2v",
    "seedance-1.0-lite-i2v",
)
_SEEDANCE_1_NO_LAST_FRAME = (
    "seedance-1-0-pro-fast",
    "seedance-1.0-pro-fast",
    "seedance-1-0-lite-t2v",
    "seedance-1.0-lite-t2v",
)
_SEEDANCE_1_REFERENCE_IMAGES = ("seedance-1-5-pro", "seedance-1.5-pro")


def _seedance_known(lowered: str, prefixes: tuple[str, ...]) -> bool:
    """已验证型号：前缀第一次出现之后只剩空串或纯数字日期戳。"""
    for prefix in prefixes:
        index = lowered.find(prefix)
        if index != -1 and _SEEDANCE_KNOWN_SUFFIX.match(lowered[index + len(prefix) :]):
            return True
    return False


def _seedance(name: str) -> Facts | None:
    lowered = name.lower()
    if "seedance-2-5" in lowered or "seedance-2.5" in lowered:
        verified = _seedance_known(lowered, _SEEDANCE_2_5_VERIFIED)
        return _video(last_frame=verified, max_reference_images=30, audio=(10, 30.0) if verified else None)
    if "seedance-2-0" in lowered or "seedance-2.0" in lowered:
        verified = _seedance_known(lowered, _SEEDANCE_2_VERIFIED)
        return _video(last_frame=verified, max_reference_images=9, audio=(3, 15.0) if verified else None)
    if "seedance-1-" not in lowered and "seedance-1." not in lowered:
        return None
    denied_last_frame = any(sub in lowered for sub in _SEEDANCE_1_NO_LAST_FRAME)
    return _video(
        first_frame=not any(sub in lowered for sub in _SEEDANCE_1_NO_FIRST_FRAME),
        last_frame=_seedance_known(lowered, _SEEDANCE_1_LAST_FRAME) and not denied_last_frame,
        max_reference_images=9 if _seedance_known(lowered, _SEEDANCE_1_REFERENCE_IMAGES) else 0,
    )


# ── Gemini Veo、OpenAI Sora、xAI Grok ────────────────────────────────────────


def _veo(name: str) -> Facts | None:
    # normalize_model_name：去命名空间、忽略大小写、点号视同连字符。
    if not name.rpartition("/")[2].lower().replace(".", "-").startswith("veo-3-1-"):
        return None
    return _video(last_frame=True, max_reference_images=3)


def _sora(name: str) -> Facts | None:
    # 首帧与参考图共用 input_reference 一个槽位。
    return _video(max_reference_images=1) if name.startswith("sora-2") else None


def _grok(name: str) -> Facts | None:
    if not name.startswith("grok-imagine-video"):
        return None
    return _video(
        last_frame=name == "grok-imagine-video-1.5",
        max_reference_images=0 if name == "grok-imagine-video-1.5-lite" else 7,
    )


# ── 可灵（lib/backends/video_backends/kling.py）────────────────────────────
# （文生视频，尾帧，参考图上限）。kling-v2-5-turbo / kling-v2-6 的尾帧只在 pro 档可用，档位约束属于调用端点，
# 模型本身接受尾帧，记 true。

_KLING: dict[str, tuple[bool, bool, int]] = {
    "kling-v2-5-turbo": (True, True, 0),
    "kling-v3": (True, True, 0),
    "kling-v3-omni": (True, True, 4),
    "kling-v2-6": (True, True, 0),
    "kling-video-o1": (False, True, 4),
}


def _kling(name: str) -> Facts | None:
    found = _KLING.get(name.replace(":", "/").rsplit("/", 1)[-1].strip().lower())
    if found is None:
        return None
    text_to_video, last_frame, max_reference_images = found
    return _video(text_to_video=text_to_video, last_frame=last_frame, max_reference_images=max_reference_images)


# ── 百炼 HappyHorse / 万相（lib/backends/video_backends/dashscope.py）──────────

_DASHSCOPE: dict[str, Facts] = {
    "happyhorse-1.1-t2v": _video(first_frame=False),
    "happyhorse-1.1-i2v": _video(),
    "happyhorse-1.1-r2v": _video(first_frame=False, max_reference_images=9),
    "happyhorse-1.0-t2v": _video(first_frame=False),
    "happyhorse-1.0-i2v": _video(),
    "happyhorse-1.0-r2v": _video(first_frame=False, max_reference_images=9),
    "wan2.7-t2v": _video(first_frame=False),
    "wan2.7-i2v": _video(),
    "wan2.7-r2v": _video(max_reference_images=5, audio=(5, None)),
    "wan3.0-video": _video(last_frame=True, max_reference_images=10, audio=(5, 15.0)),
}
_WAN3 = re.compile(r"(?<![a-z0-9])wan[-_]?3(?![a-z0-9])", re.I)
_WAN27 = re.compile(r"(?<![a-z0-9])wan[-_]?2\.7(?![a-z0-9])", re.I)
_WAN_DOT = re.compile(r"(?<![a-z0-9])wan2\.\d+(?![a-z0-9])", re.I)
_HAPPYHORSE = re.compile(r"(?<![a-z0-9])happyhorse(?![a-z0-9])", re.I)
_IMAGE_TO_VIDEO = re.compile(r"(?<![a-z0-9])image[-_]?(?:to|2)[-_]?video(?![a-z0-9])", re.I)
_VIDEOEDIT = re.compile(r"(?<![a-z0-9])video[-_]?edit(?![a-z0-9])", re.I)
_S2V_V2V = re.compile(r"(?<![a-z0-9])[sv]2v(?![a-z0-9])", re.I)


def _dashscope_key(normalized: str, keys: Iterable[str]) -> str | None:
    """按标识符边界在 ``normalized`` 里找第一个出现的已知型号。"""
    for key in keys:
        if re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", normalized):
            return key
    return None


def _dashscope(name: str) -> Facts | None:
    normalized = name.strip().lower()
    if not normalized:
        return None
    if normalized in _DASHSCOPE:
        return _DASHSCOPE[normalized]
    if _HAPPYHORSE.search(normalized):
        searched = normalized
    elif _WAN3.search(normalized):
        searched = "wan3.0-video"
    elif wan27 := _WAN27.search(normalized):
        suffix = re.sub(r"wan2\.7_", "wan2.7-", _WAN27.sub("wan2.7", normalized[wan27.start() :]))
        suffix = _IMAGE_TO_VIDEO.sub("i2v", suffix)
        wan27_keys = (key for key in _DASHSCOPE if key.startswith("wan2.7-"))
        if _VIDEOEDIT.search(suffix) or _S2V_V2V.search(suffix) or _dashscope_key(suffix, wan27_keys) is None:
            return None
        searched = normalized[: wan27.start()] + suffix
    else:
        dot = _WAN_DOT.search(normalized)
        if dot and _IMAGE_TO_VIDEO.search(normalized[dot.start() :]):
            return None
        searched = normalized
    key = _dashscope_key(searched, _DASHSCOPE)
    return _DASHSCOPE[key] if key is not None else None


# ── Vidu、Agnes、MiniMax ─────────────────────────────────────────────────────

_VIDU_TEXT2VIDEO = frozenset({"viduq3-turbo", "viduq3-pro", "viduq2", "viduq1"})
_VIDU_IMG2VIDEO = frozenset(
    {
        "viduq3-turbo",
        "viduq3-pro",
        "viduq3-pro-fast",
        "viduq2-pro-fast",
        "viduq2-pro",
        "viduq2-turbo",
        "viduq1",
        "viduq1-classic",
        "vidu2.0",
    }
)
_VIDU_START_END = frozenset(
    {
        "viduq3-turbo",
        "viduq3-pro",
        "viduq2-pro-fast",
        "viduq2-pro",
        "viduq2-turbo",
        "viduq1",
        "viduq1-classic",
        "vidu2.0",
    }
)
_VIDU_REFERENCE = frozenset({"viduq3-mix", "viduq3-turbo", "viduq3", "viduq2-pro", "viduq2", "viduq1", "vidu2.0"})


def _vidu(name: str) -> Facts | None:
    if name not in _VIDU_TEXT2VIDEO | _VIDU_IMG2VIDEO | _VIDU_START_END | _VIDU_REFERENCE:
        return None
    return _video(
        text_to_video=name in _VIDU_TEXT2VIDEO,
        first_frame=name in _VIDU_IMG2VIDEO,
        last_frame=name in _VIDU_START_END,
        max_reference_images=7 if name in _VIDU_REFERENCE else 0,
    )


_AGNES = {"agnes-video-2.5-flash": 5, "agnes-video-2.5": 8, "agnes-video-v2.0": 4}


def _agnes(name: str) -> Facts | None:
    return _video(last_frame=True, max_reference_images=_AGNES[name]) if name in _AGNES else None


def _minimax(name: str) -> Facts | None:
    # 内置 MiniMax 按名字选的声明式端点（lib/backends/backend_assembly/specs.py）与各端点定义的能力。
    lowered = name.lower()
    if name == "S2V-01":
        return _video(text_to_video=False, first_frame=False, max_reference_images=1)
    if "minimax-h3" in lowered:
        return _video(last_frame=True, max_reference_images=9, audio=(3, 15.0))
    if "hailuo" in lowered:
        return _video(text_to_video="fast" not in lowered)
    return None


_VIDEO_RULES: tuple[Callable[[str], Facts | None], ...] = (
    _seedance,
    _veo,
    _sora,
    _grok,
    _kling,
    _dashscope,
    _vidu,
    _agnes,
    _minimax,
)


# ── 图片 ─────────────────────────────────────────────────────────────────────

_SEEDREAM_5_PRO_FLASH = re.compile(r"seedream-5[-.]0-(?:pro|flash)")
_GEMINI_14_REFERENCES = frozenset(
    {"gemini-3-pro-image", "gemini-3.1-flash-image", "gemini-3-pro-image-preview", "gemini-3.1-flash-image-preview"}
)


def _gpt_image_2(name: str) -> Facts | None:
    # gpt-image-2 与它的日期快照，不含 2.5 等后续型号（lib/backends/image_backends/openai.py）。
    if name == "gpt-image-2" or name.startswith("gpt-image-2-"):
        return {"output_token_bases": {"low": 16, "medium": 48, "high": 96}}
    return None


def _image_reference_limit(name: str) -> Facts | None:
    lowered = name.lower()
    if "seedream" in lowered:
        return {"max_reference_images": 10 if _SEEDREAM_5_PRO_FLASH.search(lowered) else 14}
    if lowered.startswith("wan"):
        return {"max_reference_images": 9}
    if lowered.startswith("qwen"):
        return {"max_reference_images": 3}
    if name in _GEMINI_14_REFERENCES:
        return {"max_reference_images": 14}
    if "kling" in lowered:
        return {"max_reference_images": 10}
    return None


_IMAGE_RULES: tuple[Callable[[str], Facts | None], ...] = (_gpt_image_2, _image_reference_limit)


def _pre_upgrade_facts(media_type: str, names: Sequence[str]) -> Facts:
    """升级前按名字认定的模型事实。视频取第一条认得的通道规则；图片的估算基数与参考图上限各自认定。"""
    rules = {"video": _VIDEO_RULES, "image": _IMAGE_RULES}.get(media_type, ())
    found: Facts = {}
    for rule in rules:
        for name in names:
            facts = rule(name)
            if facts is not None:
                found.update(facts)
                break
        if media_type == "video" and found:
            break
    return found


def _backfilled(entry: object, media_type: str) -> dict[str, object] | None:
    """补齐后的副本；没有可补的字段或副本读不出时为 ``None``。"""
    if not isinstance(entry, dict) or entry.get("media_type") != media_type:
        return None
    facts = entry.get("facts")
    name = entry.get("name")
    if not isinstance(facts, dict) or not isinstance(name, str):
        return None
    aliases = entry.get("aliases")
    names = [name, *(alias for alias in aliases if isinstance(alias, str))] if isinstance(aliases, list) else [name]
    added = {key: value for key, value in _pre_upgrade_facts(media_type, names).items() if key not in facts}
    if any(key in facts for key in _AUDIO_KEYS):
        for key in _AUDIO_KEYS:
            added.pop(key, None)
    if not added:
        return None
    return {**entry, "facts": {**facts, **added}}


def upgrade() -> None:
    connection = op.get_bind()
    table = sa.table(
        "catalog_user_entry",
        sa.column("id", sa.Integer()),
        sa.column("media_type", sa.String()),
        sa.column("entry", sa.JSON()),
    )
    rows = connection.execute(sa.select(table.c.id, table.c.media_type, table.c.entry)).all()
    for row_id, media_type, entry in rows:
        updated = _backfilled(entry, media_type)
        if updated is not None:
            connection.execute(table.update().where(table.c.id == row_id).values(entry=updated))


def downgrade() -> None:
    pass
