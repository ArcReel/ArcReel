"""PROTOTYPE — 三段论书写格式解析/渲染纯逻辑（throwaway，勿 import 进生产代码）。

问题：剧集参考路径书写层从 `Shot N (Xs):` 整体改版为 Seedance 2.0 三段论后，
可试写的完整书写格式长什么样——书写层写什么、机器渲染时补什么。

本模块是纯函数集，无 I/O：
- parse_script(text)            书写文稿 → 结构（shots / mentions / utterances / warnings）
- render_backend_prompt(...)    结构 + 资产表 + 能力档 → 最终发给视频模型的三段论 prompt

书写格式提案（正文详见同目录 README.md）：
- 镜头 header：`镜头N (Xs)：`（兼容存量 `Shot N (Xs):`）；(Xs) 承载 per-shot 时长，渲染时剥除
- 首个 header 之前的自由文本 = 总体定调段（可选，机器有兜底）
- 资产引用：`@[名称]`（编辑器 mention 语法不变）；渲染时替换为 `<名称>`，
  并在第一段自动生成 `<名称>@图片N` 绑定；图片编号 = mention 首现顺序（台词 speaker 位同口径计入）
- 台词规范行：`@[名称]：{台词}` 独立成行；裸 `{台词}` 行 = 画外音。
  规范行渲染为 `<名称>说 {台词}`；音色参考与声音特征集中在第一段声明
  （官方三段论第一段即参考来源声明区，2026-07-29 拍板）。
  utterance 只从规范行派生；台词混在描述行内时原样发送、不派生、出 warning
  （不做「行内最近 mention 猜 speaker」启发式——推断错误会静默错绑参考音频）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 数据形
# ---------------------------------------------------------------------------

CapabilityTier = str  # "A" 原生音频参考 / "B" 有声无音色输入 / "C" 无声


@dataclass
class FakeAsset:
    """原型用假资产条目（对应 project.json bucket 条目 + #1383 拍板的 reference_audio）。"""

    name: str
    type: str  # character / scene / prop
    voice_style: str = ""
    has_reference_audio: bool = False


@dataclass
class Utterance:
    kind: str  # dialogue / voiceover
    speaker: str | None
    text: str
    shot_index: int


@dataclass
class ParsedShot:
    index: int
    duration: int
    lines: list[str]


@dataclass
class ParseResult:
    preamble: str
    shots: list[ParsedShot]
    mentions: list[str]  # 首现顺序去重（含台词 speaker 位）
    utterances: list[Utterance]
    warnings: list[str]
    legacy_headers: bool  # 文稿使用了存量 `Shot N (Xs):` header


@dataclass
class RenderResult:
    prompt: str
    image_order: list[str]  # @图片N → 资产名（1-based 按下标）
    audio_order: list[str]  # @音频N → 角色名
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

# 双格式 header：新 `镜头N (Xs)：` / 存量 `Shot N (Xs):`，中英冒号均可
_HEADER_RE = re.compile(
    r"^(?:镜头|Shot)\s*(\d+)\s*[（(]\s*(\d+)\s*s?\s*[)）]\s*[：:]\s*(.*)$",
    re.IGNORECASE,
)
_LEGACY_HEADER_RE = re.compile(r"^Shot\b", re.IGNORECASE)

# mention：`@[名称]`（编辑器形式）或裸 `@名称`（存量兼容）
_MENTION_RE = re.compile(r"@\[([^\]\r\n]+)\]|@([A-Za-z0-9_一-鿿]+)")

# 台词块
_BRACE_RE = re.compile(r"\{([^{}]*)\}")

# 规范台词行：`@[名称]：{台词}` 整行仅此结构；voiceover 规范行：整行仅 `{台词}`
_NORMATIVE_DIALOGUE_RE = re.compile(r"^\s*@\[?([^\]\s：:]+)\]?\s*[：:]\s*\{([^{}]*)\}\s*$")
_NORMATIVE_VOICEOVER_RE = re.compile(r"^\s*\{([^{}]*)\}\s*$")


def _mention_names(text: str) -> list[str]:
    return [m.group(1) or m.group(2) for m in _MENTION_RE.finditer(text)]


def parse_script(text: str) -> ParseResult:
    """书写文稿 → 结构。utterances / mentions 均为读时机械派生，不落盘。"""
    warnings: list[str] = []
    preamble_buf: list[str] = []
    shots: list[ParsedShot] = []
    current: ParsedShot | None = None
    legacy = False

    for raw_line in text.splitlines():
        m = _HEADER_RE.match(raw_line.strip())
        if m:
            if _LEGACY_HEADER_RE.match(raw_line.strip()):
                legacy = True
            current = ParsedShot(index=len(shots) + 1, duration=int(m.group(2)), lines=[])
            if m.group(3).strip():
                current.lines.append(m.group(3).strip())
            shots.append(current)
        elif current is not None:
            current.lines.append(raw_line)
        else:
            preamble_buf.append(raw_line)

    if not shots:
        # 无 header → 整段视为单镜头（沿用现行口径，时长由外层指定）
        shots = [ParsedShot(index=1, duration=0, lines=text.splitlines())]
        preamble_buf = []

    # mention 首现顺序 = 参考图编号。规范台词行的 speaker 位不计入：
    # 附带参考图会诱导模型把画外说话的角色画进画面（2026-07-29 拍板）
    seen: set[str] = set()
    mentions: list[str] = []

    def _collect(text: str) -> None:
        for name in _mention_names(text):
            if name not in seen:
                seen.add(name)
                mentions.append(name)

    _collect("\n".join(preamble_buf))
    for shot in shots:
        for line in shot.lines:
            if _NORMATIVE_DIALOGUE_RE.match(line):
                continue
            _collect(line)

    # utterances：逐镜逐行派生
    # utterance 只从规范行派生：不做「行内最近 mention 猜 speaker」类启发式
    # （推断错误会把错误角色静默列入参考音频；格式外一律降级 + warning）
    utterances: list[Utterance] = []
    for shot in shots:
        for line in shot.lines:
            if line.count("{") != line.count("}"):
                warnings.append(
                    f"镜头{shot.index}：台词花括号未闭合，未识别为台词，该行文本将原样发送：{line.strip()[:30]}…"
                )
                continue
            nd = _NORMATIVE_DIALOGUE_RE.match(line)
            nv = _NORMATIVE_VOICEOVER_RE.match(line)
            if nd:
                utterances.append(Utterance("dialogue", nd.group(1), nd.group(2), shot.index))
                continue
            if nv:
                utterances.append(Utterance("voiceover", None, nv.group(1), shot.index))
                continue
            if _BRACE_RE.search(line):
                warnings.append(
                    f"镜头{shot.index}：台词与描述写在同一行，未识别为台词；"
                    f"如需声音参考请将台词单独成行（@[角色]：{{台词}}）"
                )

    preamble = "\n".join(preamble_buf).strip()
    return ParseResult(preamble, shots, mentions, utterances, warnings, legacy)


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

_QUALITY_PACK = "高清，细节丰富，电影质感，色彩自然，光影柔和。"
_STABILITY_PACK = "人物面部稳定不变形、五官清晰、动作连贯自然，不僵硬，无穿模无卡顿。"
_SUBTITLE_PACK = "保持无字幕，避免生成任何文字或字幕。"
_WATERMARK_PACK = "不要生成水印；不要生成 Logo。"
_NO_BGM_PACK = "禁止出现背景音乐。"
_TWIN_PACK = (
    "视频全程禁止出现外形、着装、配饰完全一致的人物，禁止生成同款分身、"
    "双胞胎效果，同一画面中仅保留单个对应人物，不出现人物重复复刻。"
)


def _replace_mentions(line: str, registered: set[str]) -> str:
    """`@[名称]` / `@名称` → `<名称>`；未注册的原样保留。"""

    def _sub(m: re.Match[str]) -> str:
        name = m.group(1) or m.group(2)
        return f"<{name}>" if name in registered else m.group(0)

    return _MENTION_RE.sub(_sub, line)


def render_backend_prompt(
    parsed: ParseResult,
    assets: dict[str, FakeAsset],
    tier: CapabilityTier,
    style: str = "",
    max_ref_audio: int = 3,
    model_id: str = "",
) -> RenderResult:
    """结构 + 资产表 + 能力档 → 三段论 backend prompt。

    第一段（机器生成）：定调（书写层 preamble 优先）+ `<名称>@图片N` 绑定 + 声音特征描述
    第二段（书写层为主）：`镜头N：`（时长剥除）+ mention 替换 + 规范台词行重组官方句式
    第三段（机器挂载）：风格锚定 + 画质/稳定/字幕/水印包 + 按需双胞胎兜底
    """
    warnings: list[str] = []

    registered = [n for n in parsed.mentions if n in assets]
    missing = [n for n in parsed.mentions if n not in assets]
    for name in missing:
        warnings.append(f"@[{name}] 未在角色/场景/道具中登记：不会附带参考图，请检查名称或先创建资产")
    registered_set = set(registered)
    image_no = {name: i for i, name in enumerate(registered, start=1)}

    # 音频编号：dialogue speaker 首现顺序，仅 A 类、仅资产带 reference_audio
    audio_order: list[str] = []
    audio_no: dict[str, int] = {}
    if tier == "A":
        warned_speakers: set[str] = set()
        for u in parsed.utterances:
            if u.kind != "dialogue" or u.speaker is None or u.speaker in audio_no or u.speaker in warned_speakers:
                continue
            asset = assets.get(u.speaker)
            if asset is None or asset.type != "character":
                continue
            if not asset.has_reference_audio:
                warned_speakers.add(u.speaker)
                warnings.append(f"角色「{u.speaker}」未设置参考音频：台词声音将由模型自行决定")
                continue
            if len(audio_order) >= max_ref_audio:
                warned_speakers.add(u.speaker)
                warnings.append(f"参考音频最多 {max_ref_audio} 段：角色「{u.speaker}」的台词声音将由模型自行决定")
                continue
            audio_no[u.speaker] = len(audio_order) + 1
            audio_order.append(u.speaker)

    # ---- 第一段：总体设定 + 主体定义（机器生成） ----
    seg1: list[str] = []
    if parsed.preamble:
        seg1.append(_replace_mentions(parsed.preamble, registered_set))
    bindings = "、".join(f"<{n}>@图片{image_no[n]}" for n in registered)
    if bindings:
        seg1.append(bindings + "。")
    # 声音声明集中在第一段（官方三段论第一段即参考来源声明区：人脸/运镜/音色参考同位）。
    # 遍历「有台词的已登记角色」而非参考图列表——纯画外角色无参考图，但音色声明照常
    if tier in ("A", "B"):
        speakers_in_order: list[str] = []
        for u in parsed.utterances:
            if u.kind == "dialogue" and u.speaker and u.speaker in assets and u.speaker not in speakers_in_order:
                speakers_in_order.append(u.speaker)
        for name in speakers_in_order:
            parts: list[str] = []
            if name in audio_no:
                parts.append(f"台词音色参考 @音频{audio_no[name]}")
            if assets[name].voice_style:
                parts.append(f"声音特征：{assets[name].voice_style}")
            if parts:
                seg1.append(f"<{name}>的" + "，".join(parts) + "。")

    # ---- 第二段：镜头分镜 ----
    seg2: list[str] = []
    for shot in parsed.shots:
        body: list[str] = []
        for line in shot.lines:
            nd = _NORMATIVE_DIALOGUE_RE.match(line)
            nv = _NORMATIVE_VOICEOVER_RE.match(line)
            if nd:
                if nd.group(1) in assets:
                    # 音色绑定已集中在第一段声明，台词行统一简洁句式。
                    # 判定用资产表而非参考图列表：纯画外角色无参考图，台词行照常重组
                    body.append(f"<{nd.group(1)}>说 {{{nd.group(2)}}}")
                    continue
                # speaker 位不进 mentions 派生，未登记 speaker 在此单独兜 warning
                warnings.append(f"@[{nd.group(1)}] 未在角色中登记：无法确认说话人，该行按原文发送")
            if nv:
                body.append(f"画外音说 {{{nv.group(1)}}}")
                continue
            body.append(_replace_mentions(line, registered_set))
        body_text = "\n".join(ln for ln in body if ln.strip())
        seg2.append(f"镜头{shot.index}：\n{body_text}" if body_text else f"镜头{shot.index}：")

    if tier == "C" and any(u.kind == "dialogue" for u in parsed.utterances):
        warnings.append(f"当前视频模型「{model_id}」不生成音频，台词仅用于提示词参考")

    # ---- 第三段：风格 + 约束包（机器挂载） ----
    seg3: list[str] = []
    if style:
        seg3.append(f"整体视觉风格：{style}。")
    seg3.append(_QUALITY_PACK + _STABILITY_PACK)
    seg3.append(_SUBTITLE_PACK + _WATERMARK_PACK + _NO_BGM_PACK)
    character_refs = [n for n in registered if assets[n].type == "character"]
    if len(character_refs) >= 2:
        seg3.append(_TWIN_PACK)

    prompt = "\n\n".join(filter(None, ["\n".join(seg1), "\n\n".join(seg2), "\n".join(seg3)]))
    return RenderResult(prompt, registered, audio_order, warnings)


def total_duration(parsed: ParseResult) -> int:
    return sum(s.duration for s in parsed.shots)
