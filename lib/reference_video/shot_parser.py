"""参考视频 prompt 解析器：prompt ↔ Shot[]/references 双向转换。

Spec: docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md §4.3
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from lib.asset_types import BUCKET_KEY
from lib.script_models import ReferenceResource, Shot

_SHOT_HEADER_RE = re.compile(
    r"""^Shot\s+\d+\s*\(\s*(\d+)\s*s\s*\)\s*:\s*(.*)$""",
    re.IGNORECASE,
)


def _is_ascii_word_char(ch: str) -> bool:
    return ch == "_" or (ch.isascii() and ch.isalnum())


def _is_legacy_mention_char(ch: str) -> bool:
    return ch == "_" or (ch.isascii() and ch.isalnum()) or ("\u4e00" <= ch <= "\u9fff")


def _next_positions(text: str, targets: set[str]) -> list[int]:
    next_pos = [len(text)] * (len(text) + 1)
    for i in range(len(text) - 1, -1, -1):
        next_pos[i] = i if text[i] in targets else next_pos[i + 1]
    return next_pos


def _iter_mentions(text: str) -> Iterator[tuple[int, int, str]]:
    """Yield (start, end, name) for @名称 / @[名称] mentions.

    The left side of `@` must not be an ASCII word character, otherwise the text
    is treated as an email/id fragment. Wrapped mentions may contain punctuation
    but cannot cross line breaks. Curly-brace wrapping is intentionally excluded
    because the editor only writes `@[名称]` and the runtime contract stays on a
    single wrapped form.
    """
    next_square = _next_positions(text, {"]"})
    next_line_break = _next_positions(text, {"\r", "\n"})
    i = 0
    while i < len(text):
        if text[i] != "@":
            i += 1
            continue

        if i > 0 and _is_ascii_word_char(text[i - 1]):
            i += 1
            continue

        if i + 1 >= len(text):
            i += 1
            continue

        opener = text[i + 1]
        if opener == "[":
            start = i + 2
            close = next_square[start]
            if start < close < next_line_break[start]:
                yield i, close + 1, text[start:close]
                i = close + 1
                continue
            i += 1
            continue

        j = i + 1
        while j < len(text) and _is_legacy_mention_char(text[j]):
            j += 1
        if j > i + 1:
            yield i, j, text[i + 1 : j]
            i = j
            continue
        i += 1


def parse_prompt(text: str) -> tuple[list[Shot], list[str], bool]:
    """把用户书写的 prompt 文本拆为 (shots, mention_names, duration_override)。

    返回的第二项是 prompt 中出现的名字列表（保持首次出现的顺序、去重），
    由 caller 结合 project.json 分派成 ReferenceResource（本函数不区分 type）。

    - 有 `Shot N (Xs):` header → 按 header 切分；override=False
    - 无 header → 整段视为单镜头、duration 由 caller 指定；override=True

    Raises:
        pydantic.ValidationError: 当 header 中的 duration 超出 Shot.duration 的 [1, 15]
            范围时由 Shot 构造抛出；调用方（PR3 executor）须捕获并映射为用户友好错误。
    """
    lines = text.splitlines()
    segments: list[tuple[int, str]] = []
    current_duration: int | None = None
    current_buf: list[str] = []

    for line in lines:
        m = _SHOT_HEADER_RE.match(line.strip())
        if m:
            if current_duration is not None:
                segments.append((current_duration, "\n".join(current_buf).strip()))
                current_buf = [m.group(2)]
            else:
                # 首个 header 之前的非空文本保留，前置到首镜头 text
                pre_header = "\n".join(current_buf).strip()
                current_buf = [pre_header, m.group(2)] if pre_header else [m.group(2)]
            current_duration = int(m.group(1))
        else:
            current_buf.append(line)

    if current_duration is not None:
        segments.append((current_duration, "\n".join(current_buf).strip()))

    if not segments:
        # 无 header → 单镜头
        return [Shot(duration=1, text=text.strip())], _extract_mentions(text), True

    shots = [Shot(duration=d, text=t) for d, t in segments]
    mentions = _extract_mentions(text)
    return shots, mentions, False


def _extract_mentions(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for _start, _end, name in _iter_mentions(text):
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def render_prompt_for_backend(text: str, references: list[ReferenceResource]) -> str:
    """把 prompt 中的 @mention 替换为 [图N]，其中 N 是 references 列表中 1-based 序号。"""
    index_by_name: dict[str, int] = {}
    for i, ref in enumerate(references, start=1):
        index_by_name[ref.name] = i

    parts: list[str] = []
    last = 0
    for start, end, name in _iter_mentions(text):
        idx = index_by_name.get(name)
        parts.append(text[last:start])
        parts.append(f"[图{idx}]" if idx else text[start:end])  # 未注册 → 保留原样
        last = end

    parts.append(text[last:])
    return "".join(parts)


def assemble_shots_text(shots: list[Any]) -> str:
    """把 unit.shots[*].text 拼接为单一原始 prompt（渲染、@→[图N] 替换之前）。

    供入队守卫点对参考生视频做空提示词结构校验：``render_prompt_for_backend`` 对未注册
    的 @mention 保留原文、从不删字，故「拼接文本去空白后为空」等价于「渲染后为空」，
    空检查可无损地在入队侧完成。

    对畸形数据做防御性归一化（Agent 可裸写 script JSON，绕过 ProjectManager 校验）：
    非 dict 的 shot 元素跳过；``text`` 缺失或非字符串（含显式 ``null``）按空串处理——
    否则 ``str(None)`` 会得到 truthy 的 "None" 既绕过空校验又把字面量注入 backend。
    """
    parts: list[str] = []
    for s in shots:
        if not isinstance(s, dict):
            continue
        text = s.get("text")
        parts.append(text if isinstance(text, str) else "")
    return "\n".join(parts)


def compute_duration_from_shots(shots: list[Shot]) -> int:
    """把 shots 时长求和，返回整数秒。"""
    return sum(s.duration for s in shots)


def resolve_references(
    names: list[str],
    project: dict,
) -> tuple[list[ReferenceResource], list[str]]:
    """按 project.json 三 bucket 把 mention 名字分派成 ReferenceResource。

    当同一名称同时存在于多个 bucket 时，优先级为 character → scene → prop。

    Returns:
        (refs, missing): refs 保持入参顺序；missing 是没在任何 bucket 找到的名字
    """
    buckets: dict[str, dict] = {
        "character": project.get(BUCKET_KEY["character"]) or {},
        "scene": project.get(BUCKET_KEY["scene"]) or {},
        "prop": project.get(BUCKET_KEY["prop"]) or {},
    }
    refs: list[ReferenceResource] = []
    missing: list[str] = []
    for name in names:
        resolved = False
        for rtype, bucket in buckets.items():
            if name in bucket:
                refs.append(ReferenceResource(type=rtype, name=name))  # type: ignore[arg-type]
                resolved = True
                break
        if not resolved:
            missing.append(name)
    return refs, missing
