"""参考上传副本压缩。

所有 I2I / I2V / R2V 生成的唯一汇流点（``MediaGenerator`` 咽喉层）在把参考图 base64
内嵌进请求体前，对**参考上传副本**（发完即删的临时字节）做：统一 JPEG q92 + 4:4:4、
基线缩放 + 有条件透传、按角色区分（多图数组走完整梯子；首/尾帧仅超字节重编码、永不缩
尺寸）、聚合预检超保守通用上限走降档梯子、被动 413 兜底续档。

只动上传副本——源资产文件与生成产出全质量不动（4K 成品永远是 4K）。逻辑建在
``lib.image_utils.compress_image_bytes`` 之上，纯函数核心与写盘解耦以便单测。

设计见 ``docs/adr/0012-reference-compression-conservative-ceiling.md``。
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from pathlib import Path

from PIL import Image

from lib.image_utils import compress_image_bytes

# ── 保守通用上限（ArcReel 侧安全策略常量，不声称任何一家的真实数字；被动 413 自我纠正）──
# 与 lib/config/service.py 的 _DEFAULT_REFERENCE_*_MAX_BYTES 数值一致（单测断言对齐）。
DEFAULT_TOTAL_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_SINGLE_MAX_BYTES = 4 * 1024 * 1024

# 降档梯子：分辨率优先、质量地板 q80。step 0 = 基线（2048,q92）；step >= len 取地板。
_LADDER: tuple[tuple[int, int], ...] = (
    (2048, 92),
    (1536, 90),
    (1280, 90),
    (1024, 88),
)
_FLOOR: tuple[int, int] = (1024, 80)

# 统一色度抽样 4:4:4，保住带文字 sheet 的边缘（避免 4:2:0 在文字边缘色彩外溢）。
_SUBSAMPLING_444 = 0
_BASELINE_QUALITY = 92
# FRAME 永不缩尺寸：用一个远大于任何真实图片的长边上限，使 compress_image_bytes 不触发 resize。
_FRAME_NO_RESIZE_EDGE = 1_000_000

# step 0 有条件透传阈值：已是 JPEG 且小于此且尺寸合规则原样透传，避免对合理小图二压。
_PASSTHROUGH_MAX_BYTES = 1 * 1024 * 1024

# 梯子可下压的档位数（不含地板）。咽喉层被动 413 续档据此判断是否已到底。
LADDER_STEPS = len(_LADDER)


class RefRole(Enum):
    """参考图角色，决定压缩策略。"""

    ARRAY = "array"  # 多图参考数组：完整基线 + 降档梯子 + 字节预算
    FRAME = "frame"  # 单张首/尾帧：仅超字节时重编码、永不缩尺寸（护 Sora 像素匹配）


@dataclass(frozen=True)
class PayloadLimits:
    """请求体上限（per-provider 可覆盖；缺省取保守通用上限）。"""

    total_max_bytes: int = DEFAULT_TOTAL_MAX_BYTES
    single_max_bytes: int = DEFAULT_SINGLE_MAX_BYTES


@dataclass
class ReferenceSpec:
    """待压缩的参考图来源。"""

    source: Path
    label: str
    role: RefRole


@dataclass
class CompressedRef:
    """压缩后（或透传的）参考图，供咽喉层构造请求。

    path 指向发完即删的临时文件，或（透传时）原始源路径。
    """

    path: Path
    label: str
    role: RefRole


class ReferencePayloadFloorError(RuntimeError):
    """参考图压缩到地板仍超上限的用户可见硬错误。

    结构仿 ``lib.image_backends.base.ImageCapabilityError``（带 ``.code`` + ``.params``，
    供路由层 ``_t(code, **params)`` 渲染），但 ``code`` 给默认值——本异常只有一个 code，
    主动预检（压到地板仍超）与被动 413 重试耗尽都产出同一 code。
    """

    def __init__(self, code: str = "ref_payload_floor_exceeded", **params) -> None:
        self.code = code
        self.params = params
        super().__init__(code)


def _ladder_params(step: int) -> tuple[int, int]:
    """返回 step 档的 (长边上限, 质量)；step 越界取地板。"""
    if 0 <= step < LADDER_STEPS:
        return _LADDER[step]
    return _FLOOR


def _image_meta(raw: bytes) -> tuple[str, int]:
    """解码取 (格式大写, 长边像素)。调用方须保证 raw 可解码。"""
    with Image.open(BytesIO(raw)) as img:
        fmt = (img.format or "").upper()
        w, h = img.size
        return fmt, max(w, h)


def compress_single_at_step(
    raw: bytes,
    role: RefRole,
    step: int,
    *,
    single_max_bytes: int,
) -> bytes:
    """按角色 + 档位压缩单张参考图，返回 JPEG（或原样透传的）字节。

    - FRAME：永不缩尺寸；**仅当超单图预算才**重编码为 JPEG q92/4:4:4，否则原样透传——
      不因格式（PNG 等）强转，以最大限度保留首/尾帧像素（ADR：保证不使 Sora 像素匹配恶化）。
    - ARRAY：按 ``step`` 取梯子档 (edge, q) 重编码为 JPEG/4:4:4；step==0 且原图已 JPEG +
      小于透传阈值 + 尺寸合规则原样透传。
    """
    if role is RefRole.FRAME:
        if len(raw) <= single_max_bytes:
            return raw
        return compress_image_bytes(
            raw,
            max_long_edge=_FRAME_NO_RESIZE_EDGE,
            quality=_BASELINE_QUALITY,
            subsampling=_SUBSAMPLING_444,
        )

    edge, quality = _ladder_params(step)
    if step == 0 and len(raw) <= _PASSTHROUGH_MAX_BYTES:
        fmt, long_edge = _image_meta(raw)
        if fmt == "JPEG" and long_edge <= edge:
            return raw
    return compress_image_bytes(
        raw,
        max_long_edge=edge,
        quality=quality,
        subsampling=_SUBSAMPLING_444,
    )


def select_ladder_step(
    raws: list[bytes],
    roles: list[RefRole],
    limits: PayloadLimits,
    *,
    start_step: int = 0,
) -> tuple[int, list[bytes]]:
    """从 start_step 逐档下压，直到「合计 ≤ total 且每张 ≤ single」或到地板。

    单图自身超总预算时有效单图上限 = min(single, total)。

    Returns:
        (landed_step, compressed_bytes)。landed_step 是**实际落定档位**（≥ start_step）——
        主动预检可能因字节超限降到比 start_step 更深的档位，咽喉层被动 413 续档须据此续档。
        保序保数：返回列表与输入 raws 一一对应，绝不丢图。

    Raises:
        ReferencePayloadFloorError: 压到地板仍超上限。
    """
    effective_single = min(limits.single_max_bytes, limits.total_max_bytes)
    step = max(start_step, 0)
    while True:
        compressed = [
            compress_single_at_step(raw, role, step, single_max_bytes=effective_single)
            for raw, role in zip(raws, roles)
        ]
        total = sum(len(b) for b in compressed)
        # single 硬门控只管 ARRAY：FRAME 永不缩尺寸、降档对它是 no-op，单张超 single 的帧
        # 不该让主动预检在 provider 被调用前就 floor 掉（保守上限是猜的，交被动 413 兜底）。
        single_ok = all(len(b) <= effective_single for b, role in zip(compressed, roles) if role is RefRole.ARRAY)
        if total <= limits.total_max_bytes and single_ok:
            return step, compressed
        if step >= LADDER_STEPS:
            raise ReferencePayloadFloorError()
        step += 1


def _try_read_image(source: Path) -> bytes | None:
    """读取本地可解码图像字节；非本地文件 / 读不出 / 不可解码一律返回 None（交由透传）。

    用 ``load()`` 做完整解码而非 ``verify()``——后者放过截断图（header 合法但像素不全），
    会让随后的 ``compress_image_bytes`` 全解码时抛 ValueError 逃出咽喉层，破坏「压缩是优化、
    不得让原本能跑通的调用因压缩层新失败」的不变量。截断/不可解码 → 透传原路径交回 backend。
    """
    try:
        if not source.is_file():
            return None
        raw = source.read_bytes()
        with Image.open(BytesIO(raw)) as img:
            img.load()
        return raw
    except Exception:
        return None


@contextlib.contextmanager
def compressed_reference_payload(
    specs: list[ReferenceSpec],
    *,
    limits: PayloadLimits,
    start_step: int = 0,
) -> Iterator[tuple[int, list[CompressedRef]]]:
    """压缩参考图为临时文件，yield (landed_step, refs)，退出时清理临时文件。

    非本地 / 不可解码源（理论上不会发生——各 backend 本就对 reference 调 read_bytes()，
    证明 reference 必为本地文件；但 URL/data-URI/损坏文件等边角）跳过压缩、原路径透传，
    绝不 raise——压缩是优化，不得让原本能跑通的调用因压缩层新失败。

    透传项不计入 select_ladder_step 的字节预算（读不出、无从计量），梯子只在可压缩子集上
    运行；但本函数按原 spec 序位把透传项与压缩 tempfile 合并回完整列表，保 ARRAY 1:1 保数
    与 [图N] 索引对齐。

    透传（compress 返回与输入同一对象，未重编码）的项一律用**原始源路径**，不写临时副本：
    既省一次拷贝、避免 PNG 字节落进 .jpg 后缀造成 MIME 错配，也最大保真（FRAME 像素匹配）。
    被重编码的项写入临时文件，文件名**沿用源 stem**（如 ``张三.jpg``）——gemini 等后端在
    label 为空时按文件名 stem 推断参考图名称，随机临时名会注入错误标签、毁掉 I2I 角色识别。

    yield 出的 landed_step 是 select_ladder_step 的实际落定档位——被动 413 续档须据此续档
    （否则 off-by-step）。
    """
    compressible_indices: list[int] = []
    compressible_raws: list[bytes] = []
    compressible_roles: list[RefRole] = []
    for i, spec in enumerate(specs):
        raw = _try_read_image(spec.source)
        if raw is None:
            continue
        compressible_indices.append(i)
        compressible_raws.append(raw)
        compressible_roles.append(spec.role)

    landed_step, compressed_bytes = select_ladder_step(
        compressible_raws,
        compressible_roles,
        limits,
        start_step=start_step,
    )

    temp_root: Path | None = None
    try:
        by_index: dict[int, CompressedRef] = {}
        for k, (idx, data) in enumerate(zip(compressible_indices, compressed_bytes)):
            spec = specs[idx]
            if data is compressible_raws[k]:
                # 透传：未重编码，直接用原始源路径（不写临时副本）。
                by_index[idx] = CompressedRef(path=Path(spec.source), label=spec.label, role=spec.role)
                continue
            # 重编码：写临时文件，按 idx 分子目录避免重名，文件名沿用源 stem 保留参考图名。
            if temp_root is None:
                temp_root = Path(tempfile.mkdtemp(prefix="refcomp-"))
            sub = temp_root / str(idx)
            sub.mkdir()
            tmp_path = sub / f"{Path(spec.source).stem}.jpg"
            tmp_path.write_bytes(data)
            by_index[idx] = CompressedRef(path=tmp_path, label=spec.label, role=spec.role)

        merged: list[CompressedRef] = []
        for i, spec in enumerate(specs):
            ref = by_index.get(i)
            if ref is None:
                # 非本地 / 不可解码透传项：保留原路径，交回 backend 按旧行为处理。
                ref = CompressedRef(path=Path(spec.source), label=spec.label, role=spec.role)
            merged.append(ref)

        yield landed_step, merged
    finally:
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)
