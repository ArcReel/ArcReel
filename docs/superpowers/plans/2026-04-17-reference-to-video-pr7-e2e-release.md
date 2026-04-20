# 参考生视频 PR7：E2E + 发版 + 遗留问题清扫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 封顶 M6——完成参考生视频端到端集成测试、关键决策拍板、i18n/UX 查漏补缺；同时在本 PR 内一次性清扫合并前累积的 6 个遗留 issue（#340–#346 全部 OPEN 项，除已在前序 PR 解决的 #339/#342）。

**Architecture:** 本 PR 无新增架构层，只对既有模块打磨：① Pydantic/前端同步 regex 边界；② 前端 Store/Canvas/Card/Panel 的 state/a11y/perf/UX 收尾；③ 新增 `tests/integration/test_reference_video_e2e.py` 将路由→队列→executor→文件落盘跑通；④ 写回 spec 附录 B 的真实 SDK 能力矩阵。

**Tech Stack:** Python 3.11+ / pytest / FastAPI TestClient | React 19 + vitest / Testing Library / dnd-kit / zustand / Tailwind

---

## 参考文档

- Roadmap：`docs/superpowers/plans/2026-04-17-reference-to-video-roadmap.md`
- Spec：`docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md`（本 PR 需更新 **§11 未决点的决策结果** 和 **附录 B 能力矩阵**）
- 前序 PR：#328(PR1)、#330(PR2)、#332(PR3)、#337(PR6)、#338(PR4)、#342(PR5)
- 遗留 issue：#340(refactor) #341(bug) #343(a11y) #344(perf) #345(ux) #346(bug)

## 任务依赖图

```
  ┌─ T1 后端 MENTION_RE 边界  ─┐
  │                            ├─ T2 前端 MENTION_RE 边界同步（依赖 T1）
  │                            │
T3 generate_audio fallback     │
  │                            │
T4 i18n 切换提示 key ── T5 EpisodeModeSwitcher 切换确认
T6 Card 用 key 重构 ── T11 Card combobox ARIA（在 T6 后重构更清爽）
T7 Store debounce toast
T8 Panel Pill 细粒度订阅
T9 MentionPicker UX（outside-click / focus-visible / hover）
T10 Canvas 响应式布局
T12 Panel 拖拽 a11y announce
T13 tests/integration/test_reference_video_e2e.py    ← 依赖 T1-T3（行为对齐）
T14 SDK verify 脚本 + 更新 spec 附录 B
T15 决策拍板 4 项 写入 spec §11
```

推荐执行顺序：`T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14 → T15`。每个 Task 跑完都应 commit（TDD 单步，每步都能独立 revert）。

## 通用门槛

- **后端**：`uv run pytest tests/lib/test_shot_parser.py tests/server/test_reference_*.py tests/integration/ -v` 全绿；`uv run ruff check . && uv run ruff format .` 干净。
- **前端**：`cd frontend && pnpm check` 通过（typecheck + vitest + eslint + jsx-a11y）；覆盖率 ≥ 现有基线。
- **i18n**：`uv run pytest tests/test_i18n_consistency.py -v` 不报错（新增 key 必须同时在 zh/en 各一条）。
- **PR 描述**：列出本 PR 覆盖的 issue 编号与 spec §11 决策结果。

---

## Task 1：后端 `_MENTION_RE` 加前缀边界检查（issue #346 后端侧）

**Files:**
- Modify: `lib/reference_video/shot_parser.py:19, 66-74`（`_MENTION_RE` 定义 + `_extract_mentions` 调用点）
- Modify: `tests/lib/test_shot_parser.py`（新增 4 条 mention 边界 case）

**原理：** email 左侧必为 `\w`（字母/数字/下划线），中英文标点 / 空白 / 行首均不是 `\w`。用 lookbehind `(?<!\w)` 阻断 email 左侧，中文场景（`你好@张三`）不受影响（`好` 是 `\u4e00-\u9fff` 而非 `\w`）。

- [ ] **Step 1.1：写失败测试 — 后端 regex 边界**

编辑 `tests/lib/test_shot_parser.py`，在文件末尾追加：

```python
# ── mention 前缀边界（issue #346） ────────────────────────────────────────

def test_mention_ignores_email_like_prefix():
    """email 左侧是 \\w，不应被当成 mention。"""
    from lib.reference_video.shot_parser import _extract_mentions

    assert _extract_mentions("contact a@张三 for help") == []
    assert _extract_mentions("email: test@domain.com") == []
    assert _extract_mentions("alice@example.com 和 bob@foo.io") == []


def test_mention_accepts_chinese_prefix():
    """中文左侧字符（\\u4e00-\\u9fff）不是 \\w，合法 mention 用法。"""
    from lib.reference_video.shot_parser import _extract_mentions

    assert _extract_mentions("你好@张三") == ["张三"]
    assert _extract_mentions("（对面）@李四 抬眼") == ["李四"]


def test_mention_accepts_whitespace_and_line_start():
    """空白字符 / 行首 / 标点前缀都应识别。"""
    from lib.reference_video.shot_parser import _extract_mentions

    assert _extract_mentions("@张三") == ["张三"]
    assert _extract_mentions("之后 @张三 回头") == ["张三"]
    assert _extract_mentions("Shot 1 (3s):\n@张三 开门") == ["张三"]
    assert _extract_mentions("台词：@张三 起身") == ["张三"]


def test_mention_underscore_prefix_is_rejected():
    """underscore 属 \\w，`foo_@张三` 类打字错误不应触发 mention。"""
    from lib.reference_video.shot_parser import _extract_mentions

    assert _extract_mentions("prefix_@张三") == []
```

- [ ] **Step 1.2：运行失败测试**

```bash
uv run pytest tests/lib/test_shot_parser.py::test_mention_ignores_email_like_prefix tests/lib/test_shot_parser.py::test_mention_accepts_chinese_prefix tests/lib/test_shot_parser.py::test_mention_accepts_whitespace_and_line_start tests/lib/test_shot_parser.py::test_mention_underscore_prefix_is_rejected -v
```

Expected：4 条全部 FAIL（旧 regex 会误匹配 `a@张三` / `prefix_@张三`）。

- [ ] **Step 1.3：改 `_MENTION_RE` 加 lookbehind**

编辑 `lib/reference_video/shot_parser.py`，把第 19 行替换：

```python
# @名称：左侧必须不是 \w（英文词字符），否则视为 email/标识符残片而非 mention。
# Unicode CJK（\u4e00-\u9fff）不属于 \w，`你好@张三` 仍合法。
# 前后端共用约定，见 frontend/src/utils/reference-mentions.ts。
_MENTION_RE = re.compile(r"(?<!\w)@([\w\u4e00-\u9fff]+)")
```

- [ ] **Step 1.4：运行测试验证通过**

```bash
uv run pytest tests/lib/test_shot_parser.py -v
```

Expected：**全部 PASS**，包括新增 4 条和原有所有 case。

- [ ] **Step 1.5：lint + commit**

```bash
uv run ruff check lib/reference_video/shot_parser.py tests/lib/test_shot_parser.py && uv run ruff format lib/reference_video/shot_parser.py tests/lib/test_shot_parser.py
git add lib/reference_video/shot_parser.py tests/lib/test_shot_parser.py
git commit -m "fix(reference-video): MENTION_RE 加 \\w 前缀边界，避免误匹配 email (#346)"
```

---

## Task 2：前端 `MENTION_RE` 同步边界 + 所有消费点回归（issue #346 前端侧）

**Files:**
- Modify: `frontend/src/utils/reference-mentions.ts:8`（regex）
- Modify: `frontend/src/utils/reference-mentions.ts:10-21`（`extractMentions` 循环内补 guard）
- Modify: `frontend/src/hooks/useShotPromptHighlight.ts:51-71`（`pushMentionTokens` 循环内补 guard）
- Modify: `frontend/src/components/canvas/reference/ReferenceVideoCard.tsx:119-142`（`updatePickerFromCursor` 已有 `\s` guard，但行首之外仅允许空白；按新约定放宽到"非 \\w"）
- Create/Modify: `frontend/src/utils/__tests__/reference-mentions.test.ts`（若不存在则新建；补 email 等 4 组 case）
- Modify: `frontend/src/hooks/useShotPromptHighlight.test.ts`（如已存在则追加）

**原理：** JS 正则没有可变长度 lookbehind 限制问题（`(?<!\w)` 固定宽度 1，支持良好）。所有调用 `MENTION_RE` 的 `for (const m of text.matchAll(...))` 分支不再需要额外 guard，因为 regex 自身已拦截。

- [ ] **Step 2.1：写失败测试 — 前端 regex 边界**

先找到测试文件位置：

```bash
ls frontend/src/utils/__tests__/reference-mentions.test.ts 2>&1 || ls frontend/src/utils/reference-mentions.test.ts 2>&1
```

如都不存在，则创建 `frontend/src/utils/reference-mentions.test.ts`（与源文件同目录，符合项目已有 `.test.ts` 与源文件同层的惯例——检查 `ls frontend/src/utils/*.test.ts 2>&1` 确认后再放）；如已存在则在末尾追加。内容：

```typescript
import { describe, expect, it } from "vitest";
import { MENTION_RE, extractMentions, mergeReferences } from "./reference-mentions";

describe("MENTION_RE prefix boundary (#346)", () => {
  it("ignores email-like prefix", () => {
    expect(extractMentions("contact a@张三")).toEqual([]);
    expect(extractMentions("test@domain.com")).toEqual([]);
    expect(extractMentions("alice@example.com 和 bob@foo.io")).toEqual([]);
  });

  it("accepts Chinese prefix", () => {
    expect(extractMentions("你好@张三")).toEqual(["张三"]);
    expect(extractMentions("（对面）@李四")).toEqual(["李四"]);
  });

  it("accepts whitespace / line-start / punctuation prefix", () => {
    expect(extractMentions("@张三")).toEqual(["张三"]);
    expect(extractMentions("之后 @张三")).toEqual(["张三"]);
    expect(extractMentions("Shot 1 (3s):\n@张三")).toEqual(["张三"]);
    expect(extractMentions("台词：@张三")).toEqual(["张三"]);
  });

  it("rejects underscore prefix", () => {
    expect(extractMentions("prefix_@张三")).toEqual([]);
  });

  it("mergeReferences drops email-shape references", () => {
    const project = {
      characters: { 张三: { character_sheet: "c/1.png" } },
      scenes: {},
      props: {},
    } as const;
    // "a@张三" 不应被当成 mention，故 existing [] 保持空
    const refs = mergeReferences("contact a@张三", [], project as never);
    expect(refs).toEqual([]);
  });
});
```

- [ ] **Step 2.2：运行失败测试**

```bash
cd frontend && pnpm vitest run src/utils/reference-mentions.test.ts
```

Expected：5 条全部 FAIL。

- [ ] **Step 2.3：改 `MENTION_RE`**

编辑 `frontend/src/utils/reference-mentions.ts:8`：

```typescript
/**
 * Mention regex shared across frontend tokenizers. Mirrors backend
 * `lib/reference_video/shot_parser.py:_MENTION_RE` — keep in sync.
 *
 * `(?<!\w)` 拦截 email/标识符左侧的词字符（见 #346）。CJK 字符不在 \w 内，
 * 所以 `你好@张三` 仍合法。
 */
export const MENTION_RE = /(?<!\w)@([\w\u4e00-\u9fff]+)/g;
```

- [ ] **Step 2.4：简化 `ReferenceVideoCard.updatePickerFromCursor` 的 guard**

编辑 `frontend/src/components/canvas/reference/ReferenceVideoCard.tsx:119-142`，把扫描回溯的 `prev` 判断从"仅 `\s` 或行首"放宽到"非 `\w`"，与新 regex 约定对齐：

```typescript
  const updatePickerFromCursor = useCallback((nextValue: string, cursor: number) => {
    let i = cursor - 1;
    while (i >= 0) {
      const ch = nextValue[i];
      if (ch === "@") {
        const prev = nextValue[i - 1];
        // 与 MENTION_RE (?<!\w) 对齐：@ 的左侧不能是词字符，否则视为 email/id 残片。
        // 中文标点、空白、CJK 字符、行首都满足"非 \w"，不会误拦截。
        if (i === 0 || !/\w/.test(prev ?? "")) {
          atStartRef.current = i;
          setPickerQuery(nextValue.slice(i + 1, cursor));
          setPickerOpen(true);
          return;
        }
        break;
      }
      if (/\s/.test(ch)) break;
      i--;
    }
    atStartRef.current = null;
    setPickerOpen(false);
    setPickerQuery("");
  }, []);
```

- [ ] **Step 2.5：运行所有前端 reference 相关测试验证**

```bash
cd frontend && pnpm vitest run src/utils/reference-mentions.test.ts src/hooks/useShotPromptHighlight.test.ts src/components/canvas/reference/
```

Expected：全部 PASS；`useShotPromptHighlight` 的 token 切分行为不变（因为 tokenizer 里的 `matchAll(MENTION_RE)` 已自动受益于新边界）。

- [ ] **Step 2.6：commit**

```bash
git add frontend/src/utils/reference-mentions.ts frontend/src/utils/reference-mentions.test.ts frontend/src/components/canvas/reference/ReferenceVideoCard.tsx
git commit -m "fix(reference-video): 前端 MENTION_RE 与后端同步加前缀边界 (#346)"
```

---

## Task 3：`generate_audio` fallback 默认值改为 `True`（对齐 storyboard 期望）

**Files:**
- Modify: `lib/config/resolver.py:71`（`_DEFAULT_VIDEO_GENERATE_AUDIO`）
- Modify: `lib/media_generator.py:384-386`（`self._config` 为 `None` 时的硬编码 `False` 改 `True`）
- Modify: `tests/lib/test_media_generator.py` 或 `tests/lib/test_config_resolver.py`（补 fallback 行为断言）

**背景：** Spec §11 与 Roadmap PR7 关键决策："video 生成 `generate_audio` 默认应为 `True`，与 Seedance/Grok 的默认开启行为及用户期望一致"。当前两处 hardcoded `False` 与默认常量冲突。

- [ ] **Step 3.1：写失败测试 — fallback 应为 True**

编辑 `tests/lib/test_config_resolver.py`（若无则建立），追加：

```python
# ── video_generate_audio fallback（PR7 决策） ────────────────────────────

@pytest.mark.asyncio
async def test_video_generate_audio_default_is_true(tmp_path, monkeypatch):
    """无任何配置时 video_generate_audio 默认开启，与 storyboard 路径一致。"""
    from lib.config.resolver import ConfigResolver

    resolver = ConfigResolver()  # 使用内置默认
    value = await resolver.video_generate_audio()
    assert value is True, "PR7 决策：默认打开音频生成"
```

如测试文件已有 `ConfigResolver` 的 fixture，请沿用；以上仅示意 API 用法，实际 monkeypatch 细节按现有同文件内其他用例惯例补齐。

- [ ] **Step 3.2：运行测试验证失败**

```bash
uv run pytest tests/lib/test_config_resolver.py::test_video_generate_audio_default_is_true -v
```

Expected：FAIL（当前默认 False）。

- [ ] **Step 3.3：修改默认常量**

编辑 `lib/config/resolver.py:71`：

```python
    _DEFAULT_VIDEO_GENERATE_AUDIO = True
```

同步编辑 `lib/media_generator.py:384-386`（`_config` 为 `None` 的后备也改 `True`，与新默认一致）：

```python
        configured_generate_audio = (
            await self._config.video_generate_audio(self.project_name) if self._config else True
        )
```

- [ ] **Step 3.4：验证通过 + 全量相关测试**

```bash
uv run pytest tests/lib/test_config_resolver.py tests/lib/test_media_generator.py tests/lib/test_usage_tracker.py tests/lib/test_cost_calculator.py -v
```

Expected：所有 PASS；如有其他断言硬编码 `generate_audio=False`，同步校准为 `True`（逐条审视失败 case，确认是断言过时而非语义回归）。

- [ ] **Step 3.5：lint + commit**

```bash
uv run ruff check lib/config/resolver.py lib/media_generator.py tests/lib/test_config_resolver.py && uv run ruff format lib/config/resolver.py lib/media_generator.py tests/lib/test_config_resolver.py
git add lib/config/resolver.py lib/media_generator.py tests/lib/test_config_resolver.py
git commit -m "fix(config): video_generate_audio 默认改 True 对齐 storyboard 期望"
```

---

## Task 4：新增"切换生成模式"提示 i18n key

**Files:**
- Modify: `frontend/src/i18n/zh/dashboard.ts`（`episode_mode_inherit_from_project` 附近追加）
- Modify: `frontend/src/i18n/en/dashboard.ts`（同上）
- Run: `uv run pytest tests/test_i18n_consistency.py -v`

**Key 清单（zh / en 必须配对）：**

| key | zh | en |
|---|---|---|
| `episode_mode_switch_keep_data` | 切换模式不会删除已生成的 units / scenes，可随时切回原模式继续。 | Switching mode won't remove existing units / scenes — you can switch back anytime. |
| `episode_mode_switch_to_reference` | 切换到参考生视频：Timeline 中的分镜/宫格数据仍然保留，在下次切回时自动恢复。 | Switched to reference-to-video. Existing storyboard/grid data is preserved and will reappear when you switch back. |
| `episode_mode_switch_from_reference` | 切换回图生/宫格生视频：参考模式的 video_units 保留，切回时可继续生成。 | Switched back to storyboard/grid. Reference-mode video_units are preserved for later. |

- [ ] **Step 4.1：编辑 `frontend/src/i18n/zh/dashboard.ts` 第 499 行后（`episode_mode_inherit_from_project` 之后）插入**：

```typescript
  'episode_mode_switch_keep_data': '切换模式不会删除已生成的 units / scenes，可随时切回原模式继续。',
  'episode_mode_switch_to_reference': '切换到参考生视频：Timeline 中的分镜/宫格数据仍然保留，在下次切回时自动恢复。',
  'episode_mode_switch_from_reference': '切换回图生/宫格生视频：参考模式的 video_units 保留，切回时可继续生成。',
```

同位置编辑 `frontend/src/i18n/en/dashboard.ts`：

```typescript
  'episode_mode_switch_keep_data': "Switching mode won't remove existing units / scenes — you can switch back anytime.",
  'episode_mode_switch_to_reference': 'Switched to reference-to-video. Existing storyboard/grid data is preserved and will reappear when you switch back.',
  'episode_mode_switch_from_reference': 'Switched back to storyboard/grid. Reference-mode video_units are preserved for later.',
```

- [ ] **Step 4.2：运行 i18n 一致性测试**

```bash
cd frontend && pnpm test:i18n 2>&1 || pnpm vitest run src/i18n/
cd .. && uv run pytest tests/test_i18n_consistency.py -v 2>&1
```

Expected：zh/en key 一一匹配，不报 key 漂移。

- [ ] **Step 4.3：commit**

```bash
git add frontend/src/i18n/zh/dashboard.ts frontend/src/i18n/en/dashboard.ts
git commit -m "i18n: 补切换生成模式保留数据的 toast 文案"
```

---

## Task 5：`EpisodeModeSwitcher` 切换时弹 toast 告知"旧数据保留"

**Files:**
- Modify: `frontend/src/components/canvas/EpisodeModeSwitcher.tsx`（在 `onChange` wrapping 里根据 from/to 模式选 toast 文案）
- Modify: `frontend/src/components/canvas/EpisodeModeSwitcher.test.tsx`（补切换 toast 断言）

**设计：** `EpisodeModeSwitcher` 现有 `onChange(next)` 只通知父组件 PATCH。新增 **内部 wrap**：若 `next !== effective`，在触发 `onChange` 之后推 toast。toast 文案按 from/to 选择：

- `from !== "reference_video" && next === "reference_video"` → `episode_mode_switch_to_reference`
- `from === "reference_video" && next !== "reference_video"` → `episode_mode_switch_from_reference`
- 其他（storyboard ↔ grid）→ `episode_mode_switch_keep_data`

- [ ] **Step 5.1：写失败测试**

编辑 `frontend/src/components/canvas/EpisodeModeSwitcher.test.tsx`，追加：

```typescript
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";

import { EpisodeModeSwitcher } from "./EpisodeModeSwitcher";
import { useAppStore } from "@/stores/app-store";

describe("EpisodeModeSwitcher toast on mode switch", () => {
  it("shows 'switch to reference' toast when moving into reference_video", async () => {
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    const onChange = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <EpisodeModeSwitcher projectMode="storyboard" episodeMode={undefined} onChange={onChange} />
      </I18nextProvider>,
    );
    // 点 "参考生视频" 单选按钮（by accessible name）
    await userEvent.click(screen.getByRole("radio", { name: /参考生视频|Reference-to-Video/i }));
    expect(onChange).toHaveBeenCalledWith("reference_video");
    expect(pushToast).toHaveBeenCalledWith(
      expect.stringMatching(/参考生视频|reference-to-video/i),
      "info",
    );
  });

  it("shows 'switch back' toast when leaving reference_video", async () => {
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    const onChange = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <EpisodeModeSwitcher projectMode="reference_video" episodeMode="reference_video" onChange={onChange} />
      </I18nextProvider>,
    );
    await userEvent.click(screen.getByRole("radio", { name: /图生视频|Storyboard/i }));
    expect(onChange).toHaveBeenCalledWith("storyboard");
    expect(pushToast).toHaveBeenCalledWith(
      expect.stringMatching(/参考模式|reference-mode|reference_video/i),
      "info",
    );
  });

  it("no toast when clicking the already-active mode", async () => {
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    const onChange = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <EpisodeModeSwitcher projectMode="grid" episodeMode={undefined} onChange={onChange} />
      </I18nextProvider>,
    );
    await userEvent.click(screen.getByRole("radio", { name: /宫格生视频|Grid/i }));
    expect(onChange).not.toHaveBeenCalled();
    expect(pushToast).not.toHaveBeenCalled();
  });
});
```

**注意**：如果 `GenerationModeSelector` 的角色不是 `radio` 而是 `button`，请调整 `getByRole`——先跑一次 `pnpm vitest` 观察 DOM 结构。

- [ ] **Step 5.2：运行失败测试**

```bash
cd frontend && pnpm vitest run src/components/canvas/EpisodeModeSwitcher.test.tsx
```

Expected：新 3 个 case 全部 FAIL（当前无 toast 逻辑）。

- [ ] **Step 5.3：实现 toast wrap**

把 `frontend/src/components/canvas/EpisodeModeSwitcher.tsx` 全文替换为：

```typescript
import { useTranslation } from "react-i18next";
import { GenerationModeSelector } from "@/components/shared/GenerationModeSelector";
import { normalizeMode, type GenerationMode } from "@/utils/generation-mode";
import { useAppStore } from "@/stores/app-store";

export interface EpisodeModeSwitcherProps {
  /** Project-level mode, used as fallback when episode has no override. */
  projectMode: GenerationMode;
  /** Current episode-level override; undefined = inherit from project. */
  episodeMode: GenerationMode | undefined;
  /** Called with the new mode. Parent should PATCH the episode override. */
  onChange: (next: GenerationMode) => void;
}

export function EpisodeModeSwitcher({ projectMode, episodeMode, onChange }: EpisodeModeSwitcherProps) {
  const { t } = useTranslation("dashboard");
  const effective = normalizeMode(episodeMode ?? projectMode);

  // 切换时发一个 toast，明确告诉用户"旧数据保留"。
  // spec §11 拍板：不清空，允许来回切换；文案分三种（进 / 出 / 同类互切）。
  const handleChange = (next: GenerationMode) => {
    if (next === effective) return;
    onChange(next);

    const toastKey =
      next === "reference_video"
        ? "episode_mode_switch_to_reference"
        : effective === "reference_video"
          ? "episode_mode_switch_from_reference"
          : "episode_mode_switch_keep_data";
    useAppStore.getState().pushToast(t(toastKey), "info");
  };

  return (
    <div className="flex items-center gap-2 text-xs text-gray-500">
      <span>{t("episode_mode_switcher_label")}:</span>
      <GenerationModeSelector
        value={effective}
        onChange={handleChange}
        size="sm"
        name="episodeMode"
      />
      {episodeMode === undefined && (
        <span className="text-gray-600">({t("episode_mode_inherit_from_project")})</span>
      )}
    </div>
  );
}
```

- [ ] **Step 5.4：测试通过**

```bash
cd frontend && pnpm vitest run src/components/canvas/EpisodeModeSwitcher.test.tsx
```

Expected：PASS。若 `radio` 角色不匹配，调整 selector 并再跑一次。

- [ ] **Step 5.5：commit**

```bash
git add frontend/src/components/canvas/EpisodeModeSwitcher.tsx frontend/src/components/canvas/EpisodeModeSwitcher.test.tsx
git commit -m "feat(reference-video): 切换生成模式时提示旧数据保留 (PR7)"
```

---

## Task 6：`ReferenceVideoCard` 用 `key={unit.unit_id}` 替代手写派生 state（issue #340）

**Files:**
- Modify: `frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx:166`（为 `<ReferenceVideoCard>` 加 `key={selected.unit_id}`）
- Modify: `frontend/src/components/canvas/reference/ReferenceVideoCard.tsx:43-56, 104-109`（删 `syncedUnitId` + render-time setState）
- Modify: `frontend/src/components/canvas/reference/ReferenceVideoCard.test.tsx`（现有"切换 unit 重置 text"测试应仍通过——本次重构不应破坏行为，只换实现）

**原理：** React 官方推荐用 `key` 让框架自动 reset 组件 state。父组件切 unit 时 `selected.unit_id` 变 → React 卸载旧 Card 实例、新建一个，`useState` 的 initializer 会再跑一次，得到新 unit 的 prompt。避免 render 内 setState 的心智负担。

- [ ] **Step 6.1：先跑一次现有切换 unit 测试作为回归基线**

```bash
cd frontend && pnpm vitest run src/components/canvas/reference/ReferenceVideoCard.test.tsx
```

记录通过的断言数量作为 baseline。

- [ ] **Step 6.2：改 Canvas 加 key**

编辑 `frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx:166-171`：

```tsx
                <ReferenceVideoCard
                  key={selected.unit_id}
                  unit={selected}
                  projectName={projectName}
                  episode={episode}
                  onChangePrompt={handlePromptChange}
                />
```

- [ ] **Step 6.3：简化 Card 的 state**

编辑 `frontend/src/components/canvas/reference/ReferenceVideoCard.tsx`，把 42-56 行和 104-109 行合并改为：

```tsx
  // `ReferenceVideoCanvas` 已经用 key={unit.unit_id} 让 React 自动 remount 组件，
  // 所以这里只管当前 unit 的本地编辑态；切换 unit 时整个组件重建，initializer 会再跑。
  const [currentText, setCurrentText] = useState(() => unitPromptText(unit));
```

然后把后续所有 `valueState.text` / `setValueState({ text: ..., syncedUnitId: unit.unit_id })` 引用替换为 `currentText` / `setCurrentText(...)`。`setText` 也简化：

```tsx
  const setText = useCallback((next: string) => setCurrentText(next), []);
```

- [ ] **Step 6.4：全量跑 Card 测试**

```bash
cd frontend && pnpm vitest run src/components/canvas/reference/ReferenceVideoCard.test.tsx src/components/canvas/reference/ReferenceVideoCanvas.test.tsx
```

Expected：**所有 baseline 断言通过**；特别检查"切换到另一个 unit 时 textarea.value 变成新 unit 的 prompt"仍然绿。

- [ ] **Step 6.5：commit**

```bash
git add frontend/src/components/canvas/reference/ReferenceVideoCard.tsx frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx
git commit -m "refactor(reference-video): 用 key={unit_id} 替代 Card 手写派生 state (#340)"
```

---

## Task 7：debounce 保存失败时弹 toast（issue #341）

**Files:**
- Modify: `frontend/src/stores/reference-video-store.ts:207-210`（debounce catch 追加 `pushToast`）
- Modify: `frontend/src/stores/reference-video-store.test.ts`（补 toast 断言）

- [ ] **Step 7.1：写失败测试**

编辑 `frontend/src/stores/reference-video-store.test.ts`，追加：

```typescript
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { useReferenceVideoStore, _resetDebounceState } from "./reference-video-store";
import { useAppStore } from "./app-store";
import { API } from "@/api";

describe("updatePromptDebounced failure surfacing (#341)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    _resetDebounceState();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("pushes a toast when the debounced PATCH rejects", async () => {
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    vi.spyOn(API, "patchReferenceVideoUnit").mockRejectedValueOnce(
      new Error("network down"),
    );

    useReferenceVideoStore
      .getState()
      .updatePromptDebounced("p", 1, "E1U1", "Shot 1 (3s): foo", []);
    vi.advanceTimersByTime(600);
    await Promise.resolve(); // 让 rejection 传播
    await Promise.resolve();

    expect(pushToast).toHaveBeenCalledWith(
      expect.stringContaining("network down"),
      "error",
    );
  });
});
```

- [ ] **Step 7.2：运行失败测试**

```bash
cd frontend && pnpm vitest run src/stores/reference-video-store.test.ts -t "failure surfacing"
```

Expected：FAIL（当前 catch 只 set store.error，不推 toast）。

- [ ] **Step 7.3：改 catch 分支**

先在 `frontend/src/stores/reference-video-store.ts` 顶部追加导入：

```typescript
import { useAppStore } from "@/stores/app-store";
```

再把 207-210 行的 catch 改为：

```typescript
        .catch((e) => {
          if (_fetchIds.get(dkey) !== myFetchId) return;
          const msg = errMsg(e);
          // toast 走 user-visible 提示（#341）；store.error 留给页面级 banner，两者互补。
          useAppStore.getState().pushToast(msg, "error");
          set({ error: msg });
        });
```

- [ ] **Step 7.4：测试通过**

```bash
cd frontend && pnpm vitest run src/stores/reference-video-store.test.ts
```

- [ ] **Step 7.5：commit**

```bash
git add frontend/src/stores/reference-video-store.ts frontend/src/stores/reference-video-store.test.ts
git commit -m "fix(reference-video): debounce 保存失败弹 toast 反馈 (#341)"
```

---

## Task 8：`ReferencePanel.Pill` 改用字段级 selector（issue #344）

**Files:**
- Modify: `frontend/src/components/canvas/reference/ReferencePanel.tsx:45-92`（Pill 组件）
- Modify: `frontend/src/components/canvas/reference/ReferencePanel.test.tsx`（补 re-render 计数测试或 "unchanged sibling 不 re-render" 断言）

**选项 A（父层派生 → props 传入）** — 最干净，采纳。父 `ReferencePanel` 把每个 pill 的 `imagePath` 派生到一个 `Record<"type:name", string|null>` 传进来；Pill 不再 subscribe store。

- [ ] **Step 8.1：写失败测试 — render count**

编辑 `frontend/src/components/canvas/reference/ReferencePanel.test.tsx`，追加：

```typescript
import { useProjectsStore } from "@/stores/projects-store";
import { act } from "@testing-library/react";

it("Pill does not re-render when unrelated project fields change (#344)", () => {
  const renderSpy = vi.fn();
  // 用 React DevTools profiler 或简化：改某块 pill 不相关的 store 字段，count pill 内部 fingerprint 调用次数
  // ...（详细实现按现有 test 风格，调用 useProjectsStore.setState({ currentProjectData: {...} }) 后验证
  //      Pill 未触发新的 imagePath 重算——通过 mock getAssetFingerprint 并断言调用次数 ≤ 1）
});
```

如果现有测试风格不方便测 render count，可以改为"断言 Pill 组件的 `data-last-rendered-at` 时间戳（由组件内部 useRef 记录）"间接验证——但更实用的是直接通过 `React.memo` + props 语义检查。

**替代策略（优先采纳）**：直接重构代码、跑现有测试无回归即可；render count 断言可跳过（后续手工 profiler 验证）。

- [ ] **Step 8.2：重构 Pill 组件**

把 `frontend/src/components/canvas/reference/ReferencePanel.tsx` 的 Pill 与其调用处改为：

```tsx
// 顶部 import 增加 React.memo
import { memo, useMemo, useState } from "react";
// ...（已有其他 import 保留）

interface PillProps {
  refItem: ReferenceResource;
  index: number;
  projectName: string;
  imagePath: string | null;
  thumbFingerprint: string | null;
  onRemove: () => void;
}

const Pill = memo(function Pill({
  refItem,
  index,
  projectName,
  imagePath,
  thumbFingerprint,
  onRemove,
}: PillProps) {
  const { t } = useTranslation("dashboard");
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: `${refItem.type}:${refItem.name}`,
  });
  const palette = assetColor(refItem.type);
  const thumbUrl = imagePath ? API.getFileUrl(projectName, imagePath, thumbFingerprint) : null;

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs ${palette.textClass} ${palette.bgClass} ${palette.borderClass} ${isDragging ? "opacity-50" : ""}`}
    >
      <button
        type="button"
        {...attributes}
        {...listeners}
        aria-label={t("reference_panel_drag_aria", { name: refItem.name })}
        className="cursor-grab font-mono text-[10px] text-gray-500 hover:text-gray-300"
      >
        {t("reference_panel_pill_index", { n: index + 1 })}
      </button>
      {thumbUrl && (
        <img src={thumbUrl} alt="" className="h-5 w-5 rounded object-cover" />
      )}
      <span className="truncate max-w-[120px]" title={refItem.name}>@{refItem.name}</span>
      <button
        type="button"
        onClick={onRemove}
        aria-label={t("reference_panel_remove_aria", { name: refItem.name })}
        className="text-gray-500 hover:text-red-400"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
});
```

然后 `ReferencePanel` 内派生 `pillData`：

```tsx
export function ReferencePanel({ references, projectName, onReorder, onRemove, onAdd }: ReferencePanelProps) {
  const { t } = useTranslation("dashboard");
  const [pickerOpen, setPickerOpen] = useState(false);
  const characters = useProjectsStore((s) => s.currentProjectData?.characters);
  const scenes = useProjectsStore((s) => s.currentProjectData?.scenes);
  const props = useProjectsStore((s) => s.currentProjectData?.props);
  const getFp = useProjectsStore((s) => s.getAssetFingerprint);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  // 一次性把每个 reference 的 imagePath + fingerprint 派生好，避免每个 Pill 都订阅 store。
  const pillData = useMemo(() => {
    return references.map((r) => {
      let imagePath: string | null = null;
      if (r.type === "character") {
        imagePath = (characters?.[r.name] as { character_sheet?: string } | undefined)?.character_sheet ?? null;
      } else if (r.type === "scene") {
        imagePath = (scenes?.[r.name] as { scene_sheet?: string } | undefined)?.scene_sheet ?? null;
      } else if (r.type === "prop") {
        imagePath = (props?.[r.name] as { prop_sheet?: string } | undefined)?.prop_sheet ?? null;
      }
      const fp = imagePath ? getFp(imagePath) : null;
      return { ref: r, imagePath, fingerprint: fp };
    });
  }, [references, characters, scenes, props, getFp]);

  // ...（existingKeys / candidates / onDragEnd 保持原样）

  return (
    <div className="relative border-t border-gray-800 bg-gray-950/40 p-2">
      {/* ...header 保持原样... */}
      {references.length === 0 ? (
        <p className="text-xs text-gray-500">{t("reference_panel_empty")}</p>
      ) : (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext
            items={references.map((r) => `${r.type}:${r.name}`)}
            strategy={horizontalListSortingStrategy}
          >
            <div className="flex flex-wrap gap-1.5">
              {pillData.map((d, i) => (
                <Pill
                  key={`${d.ref.type}:${d.ref.name}`}
                  refItem={d.ref}
                  index={i}
                  projectName={projectName}
                  imagePath={d.imagePath}
                  thumbFingerprint={d.fingerprint}
                  onRemove={() => onRemove(d.ref)}
                />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}
      {/* ...picker 保持原样... */}
    </div>
  );
}
```

- [ ] **Step 8.3：现有测试回归**

```bash
cd frontend && pnpm vitest run src/components/canvas/reference/ReferencePanel.test.tsx
```

Expected：所有既有断言 PASS；拖拽、thumbnail、remove 行为不变。

- [ ] **Step 8.4：commit**

```bash
git add frontend/src/components/canvas/reference/ReferencePanel.tsx frontend/src/components/canvas/reference/ReferencePanel.test.tsx
git commit -m "perf(reference-video): ReferencePanel.Pill 改 memo + props 驱动 (#344)"
```

---

## Task 9：`MentionPicker` outside-click + focus-visible + 鼠标真实移动才覆盖（issue #345 条 1/4/5）

**Files:**
- Modify: `frontend/src/components/canvas/reference/MentionPicker.tsx`
- Modify: `frontend/src/components/canvas/reference/MentionPicker.test.tsx`

**本 Task 合并 issue #345 的 3 条** —— 第 2 条（setTimeout(150) hack）已在 PR #342 改好，第 3 条窄屏布局拆到 Task 10。

**改动点：**
1. 追加 `pointerdown` 全局监听，点击 listbox 外关闭 picker。
2. option 按钮加 `focus-visible:ring-1 focus-visible:ring-indigo-400`。
3. 鼠标 hover 覆盖 activeIndex 时需"鼠标真实移动"——用 `lastPointerXY` ref 记录，`onMouseMove` 时记录，`onMouseEnter` 仅在 XY 不同于上次记录时才覆盖。

- [ ] **Step 9.1：写失败测试 — outside-click**

编辑 `frontend/src/components/canvas/reference/MentionPicker.test.tsx`，追加：

```typescript
it("closes on outside pointerdown (#345)", async () => {
  const onClose = vi.fn();
  render(
    <div>
      <button data-testid="outside">outside</button>
      <MentionPicker
        open
        query=""
        candidates={{ character: [{ name: "a", imagePath: null }], scene: [], prop: [] }}
        onSelect={() => {}}
        onClose={onClose}
      />
    </div>,
  );
  await userEvent.pointer({ keys: "[MouseLeft>]", target: screen.getByTestId("outside") });
  expect(onClose).toHaveBeenCalled();
});

it("option has focus-visible ring class (#345)", () => {
  render(
    <MentionPicker
      open
      query=""
      candidates={{ character: [{ name: "a", imagePath: null }], scene: [], prop: [] }}
      onSelect={() => {}}
      onClose={() => {}}
    />,
  );
  const option = screen.getByRole("option", { name: /a/ });
  expect(option.className).toMatch(/focus-visible:ring/);
});
```

- [ ] **Step 9.2：验证失败**

```bash
cd frontend && pnpm vitest run src/components/canvas/reference/MentionPicker.test.tsx
```

Expected：2 条 FAIL。

- [ ] **Step 9.3：改 MentionPicker**

编辑 `frontend/src/components/canvas/reference/MentionPicker.tsx`：

**a.** 外层 `<div role="listbox">` 加一个 `ref`，新增 `useEffect` 挂 `pointerdown` 监听：

```tsx
  const listboxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      const el = listboxRef.current;
      if (!el) return;
      if (e.target instanceof Node && !el.contains(e.target)) {
        onClose();
      }
    };
    // capture=true 确保在 option 的 onMouseDown preventDefault 之前能拿到事件
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [open, onClose]);
```

**b.** option 按钮 className 追加 `focus-visible:ring-1 focus-visible:ring-indigo-400 focus-visible:outline-none`：

```tsx
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm transition-colors focus-visible:ring-1 focus-visible:ring-indigo-400 focus-visible:outline-none ${
                      active ? "bg-indigo-500/15 text-indigo-200" : "text-gray-300 hover:bg-gray-900"
                    }`}
```

**c.** 鼠标真实移动才覆盖 activeIndex：

在组件顶部增加：

```tsx
  const lastPointerXY = useRef<{ x: number; y: number }>({ x: -1, y: -1 });
```

option 按钮的 event handler 改为：

```tsx
                    onMouseMove={(e) => {
                      lastPointerXY.current = { x: e.clientX, y: e.clientY };
                      setActiveIndex(globalIndex);
                    }}
                    onMouseEnter={(e) => {
                      // 只在鼠标真实位移（clientX/Y 变化）时覆盖，避免列表滚动导致 pointer 漂入误覆盖键盘选中项。
                      const last = lastPointerXY.current;
                      if (last.x === e.clientX && last.y === e.clientY) return;
                      lastPointerXY.current = { x: e.clientX, y: e.clientY };
                      setActiveIndex(globalIndex);
                    }}
```

把原 `onMouseEnter` 单独调 `setActiveIndex` 的写法删掉。

**d.** `<div ref={listboxRef} role="listbox" ...>` 绑定 ref。

- [ ] **Step 9.4：测试通过**

```bash
cd frontend && pnpm vitest run src/components/canvas/reference/MentionPicker.test.tsx
```

Expected：全部 PASS。

- [ ] **Step 9.5：commit**

```bash
git add frontend/src/components/canvas/reference/MentionPicker.tsx frontend/src/components/canvas/reference/MentionPicker.test.tsx
git commit -m "ux(reference-video): MentionPicker outside-click / focus-visible / pointermove guard (#345)"
```

---

## Task 10：`ReferenceVideoCanvas` 窄屏响应式布局（issue #345 条 3）

**Files:**
- Modify: `frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx:155`（grid-cols 改为响应式）

**设计：** 窗口 < `md`（768px）时三栏 → 单列堆叠（unit list 顶、card 中、panel 底、preview 最底）。Tailwind `md:` prefix 足以覆盖，不需要 container query。

- [ ] **Step 10.1：改布局 className**

编辑 `frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx:155`：

```tsx
      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden md:grid-cols-[minmax(260px,20%)_1fr_minmax(280px,24%)]">
```

窄屏时 UnitList / 中栏 / UnitPreviewPanel 会按文档顺序自然堆叠。如 UnitList 需要限高，可在 UnitList 根元素加 `md:h-full max-h-64 md:max-h-none`——**若现有样式已经处理 overflow，先不动；** 跑手测确认后再决定是否再调。

- [ ] **Step 10.2：手测 + vitest**

```bash
cd frontend && pnpm dev &  # 启开发服
```

在浏览器打开一个 reference_video 模式剧集，DevTools 开 Responsive 模式切 375/768/1280，确认：
- 375px：三区顺序堆叠、无水平滚动条
- 768px：切回三栏
- 1280px：中栏自由伸展

```bash
cd frontend && pnpm vitest run src/components/canvas/reference/ReferenceVideoCanvas.test.tsx
```

Expected：既有断言 PASS。

- [ ] **Step 10.3：commit**

```bash
git add frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx
git commit -m "ux(reference-video): 窄屏堆叠布局 (#345)"
```

---

## Task 11：`ReferenceVideoCard` 完整 combobox ARIA + aria-label 分离 + 未知 mention describedby（issue #343 条 1/2/3）

**Files:**
- Modify: `frontend/src/components/canvas/reference/ReferenceVideoCard.tsx`（textarea 属性、live region id）
- Modify: `frontend/src/components/canvas/reference/MentionPicker.tsx`（listbox/option 稳定 id，接受外部 id prop）
- Modify: `frontend/src/i18n/{zh,en}/dashboard.ts`（新增 `reference_editor_aria_name`、`reference_editor_unknown_mentions_label`）
- Modify: `frontend/src/components/canvas/reference/ReferenceVideoCard.test.tsx`

**新增 i18n key：**

| key | zh | en |
|---|---|---|
| `reference_editor_aria_name` | Unit prompt | Unit prompt |
| `reference_editor_unknown_mentions_label` | 未注册提及 | Unregistered mentions |

**设计：**
- textarea 加 `role="combobox"` / `aria-expanded={pickerOpen}` / `aria-controls={listboxId}` / `aria-activedescendant={activeOptionId}` / `aria-autocomplete="list"`。
- listbox id 固定为 `reference-editor-picker`；option id 按 `${kind}:${name}` 拼：`reference-option-${kind}-${name}`。
- `aria-label` 改用新的简短 key `reference_editor_aria_name`（不再用长 placeholder）。
- 未知 mentions 的 live-region 外层 `<div id="reference-editor-unknown-desc" role="status" aria-live="polite">`，textarea 的 `aria-describedby="reference-editor-unknown-desc"`（只有存在未知项时 textarea 才带 describedby，否则省略）。

- [ ] **Step 11.1：补 i18n key**

编辑 `frontend/src/i18n/zh/dashboard.ts` 在 `reference_editor_placeholder` 之后：

```typescript
  'reference_editor_aria_name': 'Unit 提示词',
  'reference_editor_unknown_mentions_label': '未注册提及',
```

`frontend/src/i18n/en/dashboard.ts` 同位置：

```typescript
  'reference_editor_aria_name': 'Unit prompt',
  'reference_editor_unknown_mentions_label': 'Unregistered mentions',
```

- [ ] **Step 11.2：写失败测试 — combobox ARIA 契约**

编辑 `frontend/src/components/canvas/reference/ReferenceVideoCard.test.tsx`，追加：

```typescript
it("textarea advertises combobox ARIA contract when picker opens (#343)", async () => {
  render(<Harness unit={unitFixture} />);
  const ta = screen.getByRole("combobox");
  expect(ta).toHaveAttribute("aria-expanded", "false");
  expect(ta).toHaveAttribute("aria-controls", "reference-editor-picker");
  expect(ta).toHaveAttribute("aria-autocomplete", "list");
  expect(ta.getAttribute("aria-label")).toBe("Unit prompt"); // aria-label 不再是 placeholder

  await userEvent.type(ta, "@");
  expect(ta).toHaveAttribute("aria-expanded", "true");
  // activedescendant 指向当前 active option
  const active = screen.getByRole("option", { name: /张三/ }); // 假设 fixture 第一条角色
  expect(ta).toHaveAttribute("aria-activedescendant", active.getAttribute("id"));
});

it("unknown mentions describedby wires to live region (#343)", async () => {
  render(<Harness unit={{ ...unitFixture, shots: [{ duration: 3, text: "@未知人" }] }} />);
  const ta = screen.getByRole("combobox");
  expect(ta).toHaveAttribute("aria-describedby", "reference-editor-unknown-desc");
  const desc = document.getElementById("reference-editor-unknown-desc");
  expect(desc).toHaveAttribute("aria-live", "polite");
  expect(desc?.textContent).toMatch(/未知人/);
});
```

（`Harness` / `unitFixture` 按该测试文件已有约定，如无则参考现有 `describe` 内 setup。）

- [ ] **Step 11.3：实现 Card 改动**

在 `ReferenceVideoCard.tsx` 中：

- textarea 属性：

```tsx
        <textarea
          ref={taRef}
          value={currentText}
          onChange={handleChange}
          onKeyUp={handleCursorUpdate}
          onClick={handleCursorUpdate}
          onBlur={handleTextareaBlur}
          onScroll={onScroll}
          role="combobox"
          aria-expanded={pickerOpen}
          aria-controls="reference-editor-picker"
          aria-autocomplete="list"
          aria-activedescendant={pickerOpen ? activeOptionId : undefined}
          aria-describedby={unknownMentions.length > 0 ? "reference-editor-unknown-desc" : undefined}
          placeholder={t("reference_editor_placeholder")}
          aria-label={t("reference_editor_aria_name")}
          spellCheck={false}
          className="..."
        />
```

`activeOptionId` 需要从 MentionPicker 拿——方案是让 MentionPicker 通过 `onActiveChange?: (id: string | null) => void` 回调向父组件报告当前高亮项 id：

```tsx
  const [activeOptionId, setActiveOptionId] = useState<string | null>(null);
  // ...传给 MentionPicker: onActiveChange={setActiveOptionId}
```

- unknown mentions live region：

```tsx
      {unknownMentions.length > 0 && (
        <div
          id="reference-editor-unknown-desc"
          role="status"
          aria-live="polite"
          className="mt-2 flex flex-wrap gap-1"
        >
          <span className="sr-only">{t("reference_editor_unknown_mentions_label")}: </span>
          {unknownMentions.map((name) => {
            const palette = ASSET_COLORS.unknown;
            return (
              <span
                key={name}
                className={`rounded border px-2 py-0.5 text-[11px] ${palette.textClass} ${palette.bgClass} ${palette.borderClass}`}
              >
                {t("reference_editor_unknown_mention", { name })}
              </span>
            );
          })}
        </div>
      )}
```

- [ ] **Step 11.4：MentionPicker 支持 activeOptionId 上报与稳定 id**

编辑 `MentionPicker.tsx`：

- 顶部 `MentionPickerProps` 加可选 `onActiveChange?: (id: string | null) => void` + `listboxId?: string`。
- 生成 option id：`const optionId = \`reference-option-${kind}-${item.name}\``;
- 每个 option 按钮加 `id={optionId}`；listbox 外层 `id={listboxId ?? "reference-editor-picker"}`。
- 新增 `useEffect` 侦听 `clampedActive` 变化，根据 `flat[clampedActive]` 算出 id 后调 `onActiveChange(id)`（empty → null）。

- [ ] **Step 11.5：测试通过 + 回归**

```bash
cd frontend && pnpm vitest run src/components/canvas/reference/ src/i18n/
```

Expected：全部 PASS；特别检查 MentionPicker 现有 case（键盘上下选、Enter）未被改坏。

- [ ] **Step 11.6：commit**

```bash
git add frontend/src/components/canvas/reference/ReferenceVideoCard.tsx frontend/src/components/canvas/reference/MentionPicker.tsx frontend/src/i18n/zh/dashboard.ts frontend/src/i18n/en/dashboard.ts frontend/src/components/canvas/reference/ReferenceVideoCard.test.tsx
git commit -m "a11y(reference-video): Card 完整 combobox ARIA + aria-label 简短化 (#343)"
```

---

## Task 12：`ReferencePanel` 拖拽键盘 announce + sr-only 说明（issue #343 条 4）

**Files:**
- Modify: `frontend/src/components/canvas/reference/ReferencePanel.tsx`（DndContext `accessibility` 选项）
- Modify: `frontend/src/i18n/{zh,en}/dashboard.ts`（追加拖拽说明 + announce 模板 key）
- Modify: `frontend/src/components/canvas/reference/ReferencePanel.test.tsx`

**新增 i18n key：**

| key | zh | en |
|---|---|---|
| `reference_panel_sr_instructions` | 按 Space 键拿起项目；使用箭头键移动；再按 Space 放下；按 Esc 取消。 | Press Space to pick up the item; use arrow keys to move; press Space again to drop; Esc to cancel. |
| `reference_panel_announce_pick_up` | 已拿起 {{name}}，当前位置 {{index}}。 | Picked up {{name}} at position {{index}}. |
| `reference_panel_announce_move` | {{name}} 移动到位置 {{index}}。 | {{name}} moved to position {{index}}. |
| `reference_panel_announce_drop` | 已放下 {{name}} 在位置 {{index}}。 | Dropped {{name}} at position {{index}}. |
| `reference_panel_announce_cancel` | 已取消拖拽 {{name}}。 | Canceled drag for {{name}}. |

- [ ] **Step 12.1：补 i18n key**

在 zh/en `dashboard.ts` 的 `reference_panel_pill_index` 之后追加上述 5 对 key。

- [ ] **Step 12.2：改 DndContext `accessibility`**

编辑 `ReferencePanel.tsx`：

```tsx
  const { t } = useTranslation("dashboard");
  // ...

  const announcements = useMemo(
    () => ({
      onDragStart: ({ active }: { active: { id: string | number } }) => {
        const name = String(active.id).split(":")[1] ?? "";
        const index = references.findIndex((r) => `${r.type}:${r.name}` === active.id);
        return t("reference_panel_announce_pick_up", { name, index: index + 1 });
      },
      onDragOver: ({ active, over }: { active: { id: string | number }; over: { id: string | number } | null }) => {
        if (!over) return undefined;
        const name = String(active.id).split(":")[1] ?? "";
        const index = references.findIndex((r) => `${r.type}:${r.name}` === over.id);
        return t("reference_panel_announce_move", { name, index: index + 1 });
      },
      onDragEnd: ({ active, over }: { active: { id: string | number }; over: { id: string | number } | null }) => {
        if (!over) return undefined;
        const name = String(active.id).split(":")[1] ?? "";
        const index = references.findIndex((r) => `${r.type}:${r.name}` === over.id);
        return t("reference_panel_announce_drop", { name, index: index + 1 });
      },
      onDragCancel: ({ active }: { active: { id: string | number } }) => {
        const name = String(active.id).split(":")[1] ?? "";
        return t("reference_panel_announce_cancel", { name });
      },
    }),
    [t, references],
  );

  const screenReaderInstructions = {
    draggable: t("reference_panel_sr_instructions"),
  };

  return (
    <div className="relative border-t border-gray-800 bg-gray-950/40 p-2">
      {/* ...header... */}
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={onDragEnd}
        accessibility={{ announcements, screenReaderInstructions }}
      >
        {/* ...SortableContext / Pills 保持原样... */}
      </DndContext>
      {/* ...picker... */}
    </div>
  );
```

（注意 `DndContext` 的类型签名是 `accessibility?: DndContextProps["accessibility"]`，按库版本适配。`@dnd-kit/core` 已内置 aria-live region 和 `screenReaderInstructions` 渲染。）

- [ ] **Step 12.3：补测试**

`ReferencePanel.test.tsx` 追加：

```typescript
it("renders screen-reader drag instructions (#343)", () => {
  render(<ReferencePanel references={[...]} ... />);
  expect(
    screen.getByText(/按 Space 键拿起|Press Space to pick up/i),
  ).toBeInTheDocument();
});
```

- [ ] **Step 12.4：测试通过**

```bash
cd frontend && pnpm vitest run src/components/canvas/reference/ReferencePanel.test.tsx
```

- [ ] **Step 12.5：commit**

```bash
git add frontend/src/components/canvas/reference/ReferencePanel.tsx frontend/src/components/canvas/reference/ReferencePanel.test.tsx frontend/src/i18n/zh/dashboard.ts frontend/src/i18n/en/dashboard.ts
git commit -m "a11y(reference-video): Panel 拖拽 announce + sr-only 键盘说明 (#343)"
```

---

## Task 13：`tests/integration/test_reference_video_e2e.py` — 混合 `@` 提及 E2E

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_reference_video_e2e.py`

**定位：** 与已有 `tests/server/test_reference_video_e2e_backend.py` 的区别——本测试覆盖 **character + scene + prop 三类都命中 + shot_parser 多 shot + `[图N]` 渲染正确性** 的完整端到端链路；落盘的 mp4、thumbnail、元数据、generated_assets 字段全部断言。

- [ ] **Step 13.1：创建目录 + 空 `__init__.py`**

```bash
mkdir -p tests/integration
```

创建 `tests/integration/__init__.py`（空文件即可）。

- [ ] **Step 13.2：写 E2E 测试**

新建 `tests/integration/test_reference_video_e2e.py`：

```python
"""参考生视频完整端到端集成测试（PR7 M6）。

覆盖：
  1. 路由 POST /reference-videos/episodes/{ep}/units → unit 创建
  2. POST .../generate → GenerationQueue enqueue（mock）
  3. dispatch 到 execute_reference_video_task
  4. executor 解析 3 bucket 的 references（character + scene + prop）
  5. shot_parser 多 shot 解析 + `@名称` → `[图N]` 渲染正确性
  6. mp4 + thumbnail 落盘
  7. generated_assets.status / video_clip / video_thumbnail 写回
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x04\x00\x00\x00\x04"
    b"\x08\x02\x00\x00\x00&\x93\t)\x00\x00\x00\x13IDATx\x9cc<\x91b\xc4\x00"
    b"\x03Lp\x16^\x0e\x00E\xf6\x01f\xac\xf5\x15\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def three_bucket_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    proj_dir = projects_root / "demo"
    proj_dir.mkdir()
    for sub in ("scripts", "characters", "scenes", "props"):
        (proj_dir / sub).mkdir()
    (proj_dir / "characters" / "张三.png").write_bytes(_TINY_PNG)
    (proj_dir / "scenes" / "酒馆.png").write_bytes(_TINY_PNG)
    (proj_dir / "props" / "长剑.png").write_bytes(_TINY_PNG)

    (proj_dir / "project.json").write_text(
        json.dumps(
            {
                "title": "Demo",
                "content_mode": "reference_video",
                "generation_mode": "reference_video",
                "style": "唐风水墨",
                "characters": {
                    "张三": {"description": "主角", "character_sheet": "characters/张三.png"},
                },
                "scenes": {
                    "酒馆": {"description": "旧木酒馆", "scene_sheet": "scenes/酒馆.png"},
                },
                "props": {
                    "长剑": {"description": "铁铸长剑", "prop_sheet": "props/长剑.png"},
                },
                "episodes": [
                    {"episode": 1, "title": "江湖夜话", "script_file": "scripts/episode_1.json"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (proj_dir / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "title": "江湖夜话",
                "content_mode": "reference_video",
                "summary": "主角手持长剑进酒馆",
                "novel": {"title": "N", "chapter": "1"},
                "duration_seconds": 0,
                "video_units": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod
    from server.services import generation_tasks as gt_mod
    from server.services import reference_video_tasks as rvt_mod

    custom_pm = ProjectManager(projects_root)
    monkeypatch.setattr(router_mod, "pm", custom_pm)
    monkeypatch.setattr(router_mod, "get_project_manager", lambda: custom_pm)
    monkeypatch.setattr(gt_mod, "pm", custom_pm, raising=False)
    monkeypatch.setattr(gt_mod, "get_project_manager", lambda: custom_pm)
    monkeypatch.setattr(rvt_mod, "get_project_manager", lambda: custom_pm)

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(
        id="u1", sub="test", role="admin"
    )
    return TestClient(app), proj_dir, monkeypatch


@pytest.mark.asyncio
async def test_e2e_three_bucket_mentions_with_multi_shot(three_bucket_client):
    client, proj_dir, monkeypatch = three_bucket_client

    # 1) 新建 unit：混合 3 bucket mention + 多 shot
    prompt = (
        "Shot 1 (3s): @张三 推门进 @酒馆\n"
        "Shot 2 (4s): 近景 @张三 握紧 @长剑\n"
    )
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={
            "prompt": prompt,
            "references": [
                {"type": "character", "name": "张三"},
                {"type": "scene", "name": "酒馆"},
                {"type": "prop", "name": "长剑"},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    unit = resp.json()["unit"]
    uid = unit["unit_id"]

    # shot_parser 落地 shots[]
    assert len(unit["shots"]) == 2
    assert unit["shots"][0]["duration"] == 3
    assert unit["shots"][1]["duration"] == 4
    assert unit["duration_seconds"] == 7
    ref_names = {r["name"] for r in unit["references"]}
    assert ref_names == {"张三", "酒馆", "长剑"}
    ref_types = {(r["type"], r["name"]) for r in unit["references"]}
    assert ref_types == {("character", "张三"), ("scene", "酒馆"), ("prop", "长剑")}

    # 2) generate 入队（mock queue）
    captured: dict = {}

    async def _fake_enqueue(**kwargs):
        captured.update(kwargs)
        return {"task_id": "t-e2e", "deduped": False}

    from server.routers import reference_videos as router_mod

    fake_queue = MagicMock()
    fake_queue.enqueue_task = AsyncMock(side_effect=_fake_enqueue)
    monkeypatch.setattr(router_mod, "get_generation_queue", lambda: fake_queue)

    resp = client.post(f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/generate")
    assert resp.status_code == 202
    assert captured["task_type"] == "reference_video"
    assert captured["resource_id"] == uid

    # 3) mock backend：校验 prompt 里 @ 已替换为 [图N]，references 顺序决定编号
    captured_backend_kwargs: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured_backend_kwargs.update(kwargs)
        out = proj_dir / "reference_videos" / f"{uid}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {
        "versions": [{"created_at": "2026-04-20T12:00:00"}]
    }
    fake_video_backend = MagicMock()
    fake_video_backend.name = "ark"
    fake_video_backend.model = "doubao-seedance-2-0-260128"
    fake_generator._video_backend = fake_video_backend

    async def _fake_get_media_generator(*_a, **_k):
        return fake_generator

    from server.services import reference_video_tasks as rvt_mod

    monkeypatch.setattr(rvt_mod, "get_media_generator", _fake_get_media_generator)

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt_mod, "extract_video_thumbnail", _fake_extract)

    # 4) 直接调 executor（绕过真实 worker 轮询）
    from server.services.generation_tasks import execute_generation_task

    result = await execute_generation_task(
        {
            "task_type": "reference_video",
            "project_name": "demo",
            "resource_id": uid,
            "payload": {"script_file": "scripts/episode_1.json"},
            "user_id": "u1",
        }
    )

    # 5) 断言 prompt 渲染：@张三 → [图1]、@酒馆 → [图2]、@长剑 → [图3]
    rendered = captured_backend_kwargs["prompt"]
    assert "[图1]" in rendered  # 张三
    assert "[图2]" in rendered  # 酒馆
    assert "[图3]" in rendered  # 长剑
    assert "@张三" not in rendered  # 所有 @ 已替换
    assert "@酒馆" not in rendered
    assert "@长剑" not in rendered

    # 6) 断言 reference_images 传了 3 个临时文件
    ref_images = captured_backend_kwargs["reference_images"]
    assert len(ref_images) == 3

    # 7) 断言 mp4 + thumbnail 落盘 + generated_assets 写回
    assert result["file_path"].endswith(f"{uid}.mp4")
    assert (proj_dir / "reference_videos" / f"{uid}.mp4").exists()

    script = json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    u = next(x for x in script["video_units"] if x["unit_id"] == uid)
    ga = u["generated_assets"]
    assert ga["status"] == "completed"
    assert ga["video_clip"] == f"reference_videos/{uid}.mp4"
    assert ga["video_thumbnail"] == f"reference_videos/thumbnails/{uid}.jpg"


@pytest.mark.asyncio
async def test_e2e_missing_reference_raises(three_bucket_client):
    """把 scenes/酒馆.png 删掉，executor 应抛 MissingReferenceError。"""
    client, proj_dir, monkeypatch = three_bucket_client
    (proj_dir / "scenes" / "酒馆.png").unlink()

    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={
            "prompt": "Shot 1 (3s): @张三 进 @酒馆",
            "references": [
                {"type": "character", "name": "张三"},
                {"type": "scene", "name": "酒馆"},
            ],
        },
    )
    uid = resp.json()["unit"]["unit_id"]

    from server.services.generation_tasks import execute_generation_task
    from lib.reference_video.errors import MissingReferenceError

    with pytest.raises(MissingReferenceError) as exc:
        await execute_generation_task(
            {
                "task_type": "reference_video",
                "project_name": "demo",
                "resource_id": uid,
                "payload": {"script_file": "scripts/episode_1.json"},
                "user_id": "u1",
            }
        )
    assert any(name == "酒馆" for _, name in exc.value.missing)
```

- [ ] **Step 13.3：运行新 E2E**

```bash
uv run pytest tests/integration/test_reference_video_e2e.py -v
```

Expected：2 条 PASS。**如 FAIL**：先核对 `captured_backend_kwargs["prompt"]` 中 `[图N]` 的实际序号——`reference_video_tasks._render_unit_prompt` 按 `unit.references` 顺序编号，上文 fixture 顺序为 `character/张三 → scene/酒馆 → prop/长剑`，所以应该 1/2/3 分别对应。

- [ ] **Step 13.4：lint + 覆盖率复核**

```bash
uv run ruff check tests/integration/ && uv run ruff format tests/integration/
uv run pytest tests/integration/ tests/server/test_reference_*.py tests/lib/test_shot_parser.py --cov=lib.reference_video --cov=server.services.reference_video_tasks --cov-report=term-missing
```

Expected：新增两测试下 `lib/reference_video/` + `server/services/reference_video_tasks.py` 覆盖率 ≥ 90%；若具体行号未覆盖，补条件分支 case。

- [ ] **Step 13.5：commit**

```bash
git add tests/integration/
git commit -m "test(reference-video): 新增 E2E 集成测试覆盖三类 bucket + 多 shot (PR7)"
```

---

## Task 14：运行 SDK 验证脚本 + 回填 spec 附录 B 能力矩阵

**Files:**
- Create: `docs/verification-reports/reference-video-sdks-2026-04-20.md`（新报告）
- Modify: `docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md:506-515`（附录 B 回填）

**前置：** 需要 Ark / Grok / Gemini / OpenAI 的 API key。如任一 key 缺失，只验证已配置的供应商，在报告中标注"未验证—密钥未配置"，不阻塞 PR7。

- [ ] **Step 14.1：查看脚本 usage**

```bash
uv run python scripts/verify_reference_video_sdks.py --help
```

- [ ] **Step 14.2：按供应商逐一跑（有 key 的才跑）**

```bash
# Ark Seedance 2.0 / 2.0 fast
uv run python scripts/verify_reference_video_sdks.py --provider ark --model doubao-seedance-2-0-260128 --refs 9 --duration 8 --generate-audio
uv run python scripts/verify_reference_video_sdks.py --provider ark --model doubao-seedance-2-0-fast-pro --refs 9 --duration 8 --generate-audio

# Grok
uv run python scripts/verify_reference_video_sdks.py --provider grok --refs 7 --duration 6

# Gemini Veo
uv run python scripts/verify_reference_video_sdks.py --provider gemini --refs 3 --duration 8

# OpenAI Sora（重点：验证多图 input_reference）
uv run python scripts/verify_reference_video_sdks.py --provider openai --refs 3 --duration 8
uv run python scripts/verify_reference_video_sdks.py --provider openai --refs 1 --duration 8  # 对照
```

每次保留 stdout / stderr 输出。

- [ ] **Step 14.3：写报告**

新建 `docs/verification-reports/reference-video-sdks-2026-04-20.md`，内容（示例 skeleton，按实际结果填）：

```markdown
# 参考生视频 SDK 验证报告（2026-04-20）

## 环境

- 分支：`feature/reference-video-pr7-e2e-release`
- 脚本：`scripts/verify_reference_video_sdks.py`
- 日期：2026-04-20

## 能力矩阵（实测）

| 供应商 | 模型 | 最大参考图 | 最大时长 | multi-shot | generate_audio | 备注 |
|---|---|---|---|---|---|---|
| Ark | doubao-seedance-2-0-260128 | ... | ... | ... | ... | ... |
| Ark | doubao-seedance-2-0-fast-pro | ... | ... | ... | ... | ... |
| Grok | grok-imagine-video | ... | ... | ... | ... | 请求体实测 ... KB |
| Gemini | veo-3.0-generate-preview | ... | ... | ... | ... | 文档 3 图上限 |
| OpenAI | sora | ... | ... | ... | ... | **多图支持实测结果写在这里** |

## 每家详细输出

### Ark Seedance 2.0

（贴 stdout 关键信息，脱敏 key）

### ...

## 结论

- Sora 多图：**是 / 否 支持**——v1 决定（隐藏 / 降级单图）。
- Grok 请求体：实测 X KB，二次压缩 threshold 保持 `long_edge=1024, q=70`。
```

- [ ] **Step 14.4：回填 spec 附录 B**

编辑 `docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md:506-515`，把"待 M1 SDK 验证后回填"改为实测结果表格；在表下方加链接："详见 `docs/verification-reports/reference-video-sdks-2026-04-20.md`"。

- [ ] **Step 14.5：commit**

```bash
git add docs/verification-reports/ docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md
git commit -m "docs(reference-video): 回填 SDK 能力矩阵实测结果 (PR7 M6)"
```

---

## Task 15：spec §11 未决点 4 项决策拍板写入

**Files:**
- Modify: `docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md:444-453`（§11 未决/留给实施计划）

**决策（按 Roadmap PR7 与本 PR 已实现的代码为准）：**

1. **`generate_audio` fallback**：**True**（Task 3 已改）
2. **切换 `generation_mode` 是否清空旧数据**：**不清空**（Task 5 的 toast 已明示）
3. **是否 bump `schema_version` v2**：**不 bump**（`generation_mode` 缺省按 `effective_mode()` 回退 storyboard，`video_units` 仅在新模式下写入，对旧项目零影响）
4. **Sora 参考模式是否隐藏**：**根据 Task 14 的实测结果**——若 Sora 多图可用（≥ 2 张）则保留；若只能单图则在 `GenerationModeSelector` 的 Sora 路径 warn 但不隐藏；若完全不支持 reference 则在前端下拉隐藏 Sora 的参考模式选项（同时后端 `_apply_provider_constraints` 仍保留 `ref_sora_single_ref` warning 作为兜底）

- [ ] **Step 15.1：改写 §11**

把 `docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md:444-453` 整节替换为：

```markdown
## 11. 已决议（PR7 M6 结论，取代原"未决"段）

> PR7（2026-04-20）把原 M6 里遗留的 4 个决策点逐条落地如下。所有项都已反映到代码与 i18n 文案。

- **`generate_audio` 默认值**：改为 `True`（`lib/config/resolver.py:71 _DEFAULT_VIDEO_GENERATE_AUDIO = True`；`lib/media_generator.py` `_config is None` 的 fallback 同改 `True`）。理由：与 Seedance / Grok 默认开启一致，storyboard 用户期望已如此。
- **集级 `generation_mode` 切换策略**：**不清空** 旧数据；`EpisodeModeSwitcher` 改为在切换时弹 toast 明示"旧数据保留，可随时切回继续"（对应 i18n key：`episode_mode_switch_to_reference` / `episode_mode_switch_from_reference` / `episode_mode_switch_keep_data`）。Canvas 继续按 `effective_mode` 渲染对应视图。
- **`schema_version`**：**不 bump**，继续 v1。新增的 `generation_mode` 顶层字段与 `video_units[]` 子树对旧项目缺省不可见；`effective_mode()` 缺省回退 `storyboard`，所以 v0→v1 迁移器无需改动。
- **Sora 参考模式可见性**：基于附录 B SDK 验证实测结果——
  - **若 ≥ 2 张 `input_reference` 可用**：前端保留 Sora 为可选项，后端 `_apply_provider_constraints` 的 `ref_too_many_images` 按实测上限 clamp。
  - **若仅单图可用**：保留可选，走现有 `ref_sora_single_ref` warn + `references[:1]` 降级分支。
  - **若完全不支持 reference**：前端 `GenerationModeSelector` 在 Sora 路径隐藏"参考生视频"；后端 executor 额外 fail early。
  - 当前（附录 B 更新时）的选择见 `docs/verification-reports/reference-video-sdks-2026-04-20.md`。

（原 §11 中 subagent prompt 模板留给 M5 的细节已在 PR6 #337 落地；本节剩余不再有"未决"项。）
```

- [ ] **Step 15.2：commit**

```bash
git add docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md
git commit -m "docs(reference-video): spec §11 四项决策落地为结论 (PR7 M6)"
```

---

## 最终验收门槛

- [ ] `uv run pytest tests/integration/ tests/lib/test_shot_parser.py tests/server/test_reference_*.py tests/lib/test_config_resolver.py tests/lib/test_media_generator.py -v` 全绿
- [ ] `uv run ruff check . && uv run ruff format .` 干净
- [ ] `cd frontend && pnpm check` 通过
- [ ] `uv run pytest tests/test_i18n_consistency.py -v` 通过
- [ ] 覆盖率 `uv run pytest --cov=lib.reference_video --cov=server.services.reference_video_tasks --cov=lib.config.resolver` ≥ 90%（新/改模块）
- [ ] 手测：浏览器打开 reference_video 模式剧集
  - [ ] `@` 触发 picker、选中候选插入、键盘 ↑↓/Enter 正常
  - [ ] 切换 unit 后 prompt 文本正确重置（验证 Task 6 重构未破坏）
  - [ ] 关闭 devtools 网络后编辑 prompt，500ms 后应看到 toast（验证 Task 7）
  - [ ] 切换生成模式时看到相应 toast（验证 Task 5）
  - [ ] 窄屏（< 768px）三区堆叠无横向滚动（验证 Task 10）
  - [ ] 键盘 Tab 到 option 时可见 focus-visible 环（验证 Task 9）
  - [ ] `@张三` 在 `email@domain.com` 这类文本里不误触（验证 Task 2）
- [ ] PR 描述列出：关联 issue 编号（#340 #341 #343 #344 #345 #346）、Roadmap PR7 6 项范围、spec §11 四项决策、SDK 验证报告链接

## 回滚策略

- 每个 Task 独立 commit，可按 issue 粒度 revert 任一任务；Task 3（`generate_audio` 默认）影响面最大，必要时单独 revert 该 commit 即可把 fallback 调回 `False`，其他模块不受牵连。
- Task 14 的报告与 Task 15 的 spec 更新仅为文档，回滚无风险。
- Task 13 新测试文件独立，revert 不会触碰生产代码。
