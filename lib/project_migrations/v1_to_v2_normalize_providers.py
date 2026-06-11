# ⏳ 时限模块：2026-06-21 后整体移除（含本文件的 legacy 别名表）。
#
# 不同于永久性的 v0→v1，这是一次性「干净断裂」迁移：把存量 project.json 里的 legacy
# provider 名（gemini/aistudio/vertex/seedance）一次性归一化为规范 id，并把 legacy 单字段
# image_backend 拆为 image_provider_t2i/i2i，此后解析链假设输入即规范 id、不再做读时归一化。
#
# 移除本模块时需一并确认：① 无其他代码引用 _LEGACY_PROVIDER_ALIASES；② 取舍——届时仍停留在
# schema_version=1 的项目（如旧备份/旧导出包还原）将不再自动迁移（已接受）。
"""v1→v2 迁移：归一化 legacy provider 名 + 拆分 legacy image_backend 字段。"""

from __future__ import annotations

from pathlib import Path

from lib.config.registry import PROVIDER_REGISTRY
from lib.json_io import atomic_write_json, load_json

# 系统中**唯一**一份 legacy 别名表（旧三处映射表的并集，不发明新别名）。
# 校验：所有目标必须是 PROVIDER_REGISTRY 中真实存在的规范 id。
_LEGACY_PROVIDER_ALIASES: dict[str, str] = {
    "gemini": "gemini-aistudio",
    "aistudio": "gemini-aistudio",
    "vertex": "gemini-vertex",
    "seedance": "ark",
}

assert all(target in PROVIDER_REGISTRY for target in _LEGACY_PROVIDER_ALIASES.values()), (
    "legacy 别名目标必须是规范 provider id"
)

# 承载 provider id 的 7 个 project.json 字段，逐个归一化。
_PROVIDER_FIELDS: tuple[str, ...] = (
    "video_backend",
    "image_backend",
    "image_provider_t2i",
    "image_provider_i2i",
    "text_backend_script",
    "text_backend_overview",
    "text_backend_style",
)


def _normalize_field(value: str) -> str:
    """归一化单个 ``"provider/model"`` 或裸 ``"provider"`` 字段的 provider 部分。

    先 strip 再比对别名表：带空白的 legacy 名（如 ``" gemini / model "``）若不 strip 会比对落空、
    残留未归一化，违背一次性「干净断裂」的目的。"""
    if "/" in value:
        provider, model = value.split("/", 1)
        provider, model = provider.strip(), model.strip()
        canonical = _LEGACY_PROVIDER_ALIASES.get(provider, provider)
        # model 缺失（如 "gemini /"）只返回规范 provider，避免留下带尾斜杠的非规范字符串
        return f"{canonical}/{model}" if model else canonical
    stripped = value.strip()
    return _LEGACY_PROVIDER_ALIASES.get(stripped, stripped)


def migrate_project_dict(project: dict) -> dict:
    """纯函数：把 v1 形态的 project dict 转为 v2 形态。幂等。

    顺序：① legacy image_backend 且 t2i/i2i 缺失时拆成两字段（拷贝原值）；
    ② 逐个归一化 7 个 provider 字段的 provider 部分；③ 删除 legacy image_backend 键。
    不改 schema_version（由文件级 migrate 提交时写入）。
    """
    data = dict(project)

    # ① 字段拆分：legacy image_backend → image_provider_t2i / _i2i（仅填补缺失槽）
    legacy = data.get("image_backend")
    if isinstance(legacy, str) and legacy:
        data.setdefault("image_provider_t2i", legacy)
        data.setdefault("image_provider_i2i", legacy)

    # ② 归一化 provider 字段
    for key in _PROVIDER_FIELDS:
        value = data.get(key)
        if isinstance(value, str) and value:
            data[key] = _normalize_field(value)

    # ③ 删除 legacy 单字段
    data.pop("image_backend", None)

    return data


def migrate_v1_to_v2(project_dir: Path) -> None:
    """v1→v2 文件级迁移。单次原子写，天然崩溃可重试（要么旧值要么新值，无半态）。"""
    pj = project_dir / "project.json"
    if not pj.exists():
        return
    data = load_json(pj)
    # or 0：显式 null 与字段缺失同义（v0），直接比较 None >= 2 会 TypeError
    if (data.get("schema_version") or 0) >= 2:
        return
    migrated = migrate_project_dict(data)
    migrated["schema_version"] = 2
    atomic_write_json(pj, migrated)
