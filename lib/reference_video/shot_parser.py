"""参考视频 prompt 解析器：prompt ↔ Shot[]/references 双向转换。

Spec: docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md §4.3
"""

from __future__ import annotations

import re

from lib.script_models import ReferenceResource, Shot

_SHOT_HEADER_RE = re.compile(
    r"""^Shot\s+\d+\s*\(\s*(\d+)\s*s\s*\)\s*:\s*(.*)$""",
    re.IGNORECASE,
)

# @名称：Unicode 字母/数字/下划线；不吞 @ 之前的字符
_MENTION_RE = re.compile(r"@([\w\u4e00-\u9fff]+)")


def parse_prompt(text: str) -> tuple[list[Shot], list[str], bool]:
    """把用户书写的 prompt 文本拆为 (shots, mention_names, duration_override)。

    返回的第二项是 prompt 中出现的名字列表（保持首次出现的顺序、去重），
    由 caller 结合 project.json 分派成 ReferenceResource（本函数不区分 type）。

    - 有 `Shot N (Xs):` header → 按 header 切分；override=False
    - 无 header → 整段视为单镜头、duration 由 caller 指定；override=True
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
            current_duration = int(m.group(1))
            current_buf = [m.group(2)]
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
    seen: list[str] = []
    for m in _MENTION_RE.finditer(text):
        name = m.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def render_prompt_for_backend(text: str, references: list[ReferenceResource]) -> str:
    """把 prompt 中的 @名称 替换为 [图N]，其中 N 是 references 列表中 1-based 序号。"""
    index_by_name: dict[str, int] = {}
    for i, ref in enumerate(references, start=1):
        index_by_name[ref.name] = i

    def _repl(m: re.Match[str]) -> str:
        name = m.group(1)
        idx = index_by_name.get(name)
        return f"[图{idx}]" if idx else m.group(0)  # 未注册 → 保留原样

    return _MENTION_RE.sub(_repl, text)


def compute_duration_from_shots(shots: list[Shot]) -> int:
    """把 shots 时长求和，返回整数秒。"""
    return sum(s.duration for s in shots)
