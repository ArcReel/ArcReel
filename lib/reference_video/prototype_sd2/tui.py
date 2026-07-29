"""PROTOTYPE — 三段论书写格式终端演示壳（throwaway）。

运行：uv run python lib/reference_video/prototype_sd2/tui.py
自检：uv run python lib/reference_video/prototype_sd2/tui.py --dump
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import termios
import tty

from logic import ParseResult, RenderResult, parse_script, render_backend_prompt, total_duration
from samples import ASSETS, SAMPLES

BOLD, DIM, RESET = "\x1b[1m", "\x1b[2m", "\x1b[0m"
YELLOW = "\x1b[33m"

TIERS = ["A", "B", "C"]
TIER_LABELS = {
    "A": "A 类：原生音频参考（Seedance 2.0 / Wan2.7）",
    "B": "B 类：有声无音色输入（voice_style 软约束）",
    "C": "C 类：无声模型",
}
# 原型占位 id，实现时取项目实际选定的 video model id
TIER_MODEL_IDS = {"A": "demo-model-a", "B": "demo-model-b", "C": "demo-model-c"}
STYLE = "烟雨都市电影感，冷调低饱和"


class State:
    def __init__(self) -> None:
        self.sample_idx = 0
        self.tier_idx = 0
        self.view = "r"  # i 输入 / d 派生 / r 渲染
        self.edited: dict[int, str] = {}  # 用户经 $EDITOR 改过的样例文本

    @property
    def tier(self) -> str:
        return TIERS[self.tier_idx]

    @property
    def sample_text(self) -> str:
        return self.edited.get(self.sample_idx, SAMPLES[self.sample_idx][1])


def _derive(state: State) -> tuple[ParseResult, RenderResult]:
    parsed = parse_script(state.sample_text)
    rendered = render_backend_prompt(parsed, ASSETS, state.tier, style=STYLE, model_id=TIER_MODEL_IDS[state.tier])
    return parsed, rendered


def _render_view(state: State) -> str:
    parsed, rendered = _derive(state)
    lines: list[str] = []
    if state.view == "i":
        lines.append(f"{BOLD}── 书写层输入（unit.shots[*].text，唯一真相）──{RESET}")
        lines.append(state.sample_text)
    elif state.view == "d":
        lines.append(f"{BOLD}── 读时机械派生（不落盘）──{RESET}")
        lines.append(f"{BOLD}shots{RESET}（header 的 (Xs) 是书写语法，解析即落 Shot.duration 结构字段）：")
        for s in parsed.shots:
            first = next((ln.strip() for ln in s.lines if ln.strip()), "")
            lines.append(f"  镜头{s.index}  {s.duration}s  {DIM}{first[:38]}…{RESET}")
        lines.append(f"  {DIM}unit 总时长 = {total_duration(parsed)}s（入队校验/计费预估口径不变）{RESET}")
        lines.append(f"{BOLD}references{RESET}（mention 首现顺序 = @图片N 编号，台词 speaker 位不计入）：")
        for i, name in enumerate(rendered.image_order, start=1):
            lines.append(f"  @图片{i} ← {name}（{ASSETS[name].type}）")
        lines.append(f"{BOLD}reference_audio_files{RESET}（dialogue speaker 首现顺序 = @音频N，仅 A 类）：")
        if rendered.audio_order:
            for i, name in enumerate(rendered.audio_order, start=1):
                lines.append(f"  @音频{i} ← {name}")
        else:
            lines.append(f"  {DIM}（空）{RESET}")
        lines.append(f"{BOLD}utterances{RESET}（逐句 speaker，镜头归属）：")
        if parsed.utterances:
            for u in parsed.utterances:
                who = u.speaker if u.kind == "dialogue" else "画外音"
                lines.append(f"  镜头{u.shot_index}  [{u.kind}] {who}: {{{u.text[:24]}}}")
        else:
            lines.append(f"  {DIM}（空 → 自然回落 B/C 类口径）{RESET}")
        if parsed.legacy_headers:
            lines.append(f"{DIM}（存量 `Shot N (Xs):` header，双格式兼容解析）{RESET}")
    else:
        lines.append(f"{BOLD}── 渲染输出（发给视频模型的最终 prompt）──{RESET}")
        lines.append(rendered.prompt)

    warns = parsed.warnings + rendered.warnings
    if warns:
        lines.append("")
        lines.append(f"{BOLD}{YELLOW}⚠ 解析/渲染 warnings（= 编辑器降级可见性面板的内容源）{RESET}")
        for w in warns:
            lines.append(f"{YELLOW}  · {w}{RESET}")
    return "\n".join(lines)


def _frame(state: State) -> str:
    name = SAMPLES[state.sample_idx][0]
    edited = "（已编辑）" if state.sample_idx in state.edited else ""
    header = (
        f"{BOLD}三段论书写格式原型{RESET}  样例 {state.sample_idx + 1}/{len(SAMPLES)}：{name}{edited}\n"
        f"能力档：{TIER_LABELS[state.tier]}\n" + "─" * 72
    )
    footer = (
        "─" * 72 + f"\n{BOLD}[1-{len(SAMPLES)}]{RESET}{DIM} 切样例  {RESET}"
        f"{BOLD}[t]{RESET}{DIM} 切能力档  {RESET}"
        f"{BOLD}[i/d/r]{RESET}{DIM} 输入/派生/渲染  {RESET}"
        f"{BOLD}[e]{RESET}{DIM} 编辑当前样例（$EDITOR）  {RESET}"
        f"{BOLD}[q]{RESET}{DIM} 退出{RESET}"
    )
    return f"{header}\n{_render_view(state)}\n{footer}"


def _getch() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _edit_sample(state: State) -> None:
    editor = os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(state.sample_text)
        path = f.name
    subprocess.run([editor, path], check=False)
    with open(path, encoding="utf-8") as f:
        state.edited[state.sample_idx] = f.read()
    os.unlink(path)


def main() -> None:
    if "--dump" in sys.argv:
        state = State()
        for i in range(len(SAMPLES)):
            state.sample_idx = i
            for view in ("i", "d", "r"):
                state.view = view
                print(_frame(state))
                print()
        return

    state = State()
    while True:
        print("\033[2J\033[H" + _frame(state))
        ch = _getch()
        if ch == "q" or ch == "\x03":
            break
        if ch.isdigit() and 1 <= int(ch) <= len(SAMPLES):
            state.sample_idx = int(ch) - 1
        elif ch == "t":
            state.tier_idx = (state.tier_idx + 1) % len(TIERS)
        elif ch in ("i", "d", "r"):
            state.view = ch
        elif ch == "e":
            _edit_sample(state)
    print()


if __name__ == "__main__":
    main()
