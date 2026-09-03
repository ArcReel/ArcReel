import { Fragment, useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { MENTION_PICKER_DEFAULT_ID, MentionPicker, type MentionCandidate } from "./MentionPicker";
import { ASSET_COLORS, assetColor } from "./asset-colors";
import { useUnitPromptHighlight, type Token } from "@/hooks/useUnitPromptHighlight";
import { buildMentionLookup, characterReferenceForms, MENTION_RE } from "@/utils/reference-mentions";
import { useProjectsStore } from "@/stores/projects-store";
import {
  SHEET_FIELD,
  type AssetKind,
  type ReferenceVideoUnit,
} from "@/types/reference-video";

// mention 胶囊改用 inline box-shadow 伪描边 + 背景色，不额外占宽：以前用 `px-0.5`
// 每遇到一个 mention 视觉层比 textarea 字符宽度多 4px，导致光标定位与可见字符偏移。
// 该类只应用颜色/背景/圆角，不改变盒模型宽度。
const MENTION_SPAN_CLASS = "rounded-sm";

/**
 * 渲染 pre 层的 token span 串；当 caretOffset 命中某个 token 内部或边界时，在该位置
 * 切一刀并插入一个零尺寸 caret anchor（给 picker 定位用）。
 *
 * anchor 只在 pickerOpen 下被使用（调用方传 null 时跳过插入）。为避免 anchor 的
 * inline-block 影响行内 layout，用 `w-0 h-[1em] inline-block align-baseline`。
 *
 * anchor 元素通过 callback ref 回传给调用方，再交给 floating-ui 做 portal 定位。
 */
function renderHighlightedTokens(
  tokens: Token[],
  caretOffset: number | null,
  setAnchorEl: (el: HTMLSpanElement | null) => void,
  voiceoverLabel: string,
): ReactNode {
  const out: ReactNode[] = [];
  let acc = 0;
  const anchorEl = caretOffset !== null ? (
    <span
      key="__caret_anchor__"
      ref={setAnchorEl}
      aria-hidden="true"
      className="inline-block h-[1em] w-0 align-baseline"
    />
  ) : null;

  const renderPiece = (tk: Token, sliceText: string, key: string): ReactNode => {
    if (sliceText.length === 0) return null;
    if (tk.kind === "mention") {
      const palette = assetColor(tk.assetKind);
      return (
        <span key={key} className={`${MENTION_SPAN_CLASS} ${palette.textClass} ${palette.bgClass}`}>
          {sliceText}
        </span>
      );
    }
    if (tk.kind === "speech") {
      // 与预览的 `ScriptHighlight` 同一套着色：底色标出「这段被认成台词了」，说话人配色跟
      // 资产档位走（画外音无说话人，恒是 unknown 档）。原文逐字保留，只加颜色与 title。
      const palette = assetColor(tk.speakerKind);
      return (
        <span
          key={key}
          className={`${MENTION_SPAN_CLASS} bg-[oklch(1_0_0_/_0.06)] ${tk.speaker ? palette.textClass : ""}`}
          title={tk.speaker || voiceoverLabel}
        >
          {sliceText}
        </span>
      );
    }
    return <span key={key}>{sliceText}</span>;
  };

  let inserted = false;
  tokens.forEach((tk, i) => {
    const nextAcc = acc + tk.text.length;
    if (!inserted && caretOffset !== null && caretOffset >= acc && caretOffset <= nextAcc) {
      const local = caretOffset - acc;
      out.push(<Fragment key={`pre-${i}`}>{renderPiece(tk, tk.text.slice(0, local), `pre-${i}`)}</Fragment>);
      if (anchorEl) out.push(anchorEl);
      out.push(<Fragment key={`post-${i}`}>{renderPiece(tk, tk.text.slice(local), `post-${i}`)}</Fragment>);
      inserted = true;
    } else {
      out.push(<Fragment key={`t-${i}`}>{renderPiece(tk, tk.text, `t-${i}`)}</Fragment>);
    }
    acc = nextAcc;
  });
  if (!inserted && anchorEl && caretOffset !== null && caretOffset >= acc) {
    out.push(anchorEl);
  }
  return out;
}

export interface ReferenceVideoCardProps {
  unit: ReferenceVideoUnit;
  projectName: string;
  episode: number;
  /** Controlled value — parent owns the draft/saved state. */
  value: string;
  /** Fires on every edit; parent decides whether to debounce, persist, or queue. */
  onChange: (next: string) => void;
}

export function ReferenceVideoCard({
  unit,
  projectName,
  episode: _episode,
  value,
  onChange,
}: ReferenceVideoCardProps) {
  const { t } = useTranslation("dashboard");
  const taRef = useRef<HTMLTextAreaElement>(null);
  const preRef = useRef<HTMLPreElement>(null);
  // caret anchor 通过 callback ref 转成 state，供 MentionPicker 的 floating-ui
  // 作为参考元素。使用 state 而非 ref：floating-ui 的 `refs.setReference` 必须
  // 在 reference 元素变更时感知到（初次 null → 挂载后的 span），state 变更会触发
  // Picker 的 effect 重新 setReference。
  const [anchorEl, setAnchorEl] = useState<HTMLSpanElement | null>(null);

  // 父组件把 prompt 当作受控值传入（可能是服务端原值，也可能是未保存的草稿）。
  // 本地 state 仅保留 MentionPicker 相关的 UI 细节。
  const currentText = value;

  const project = useProjectsStore((s) => s.currentProjectData);

  const lookup = useMemo(() => buildMentionLookup(project), [project]);

  const tokens = useUnitPromptHighlight(currentText, lookup);

  const voiceoverLabel = t("script_highlight_voiceover");

  // pickerOpen=false 是绝对多数路径（打字时 picker 只在 @ 触发短暂打开）。
  // tokens 已被 useUnitPromptHighlight memo 化，这里再把 tokens→ReactNode 列表缓存一层，
  // 父组件或其他 state 引起的 re-render 就不会重新跑 renderHighlightedTokens 的 forEach。
  const staticHighlightedNodes = useMemo(
    () => renderHighlightedTokens(tokens, null, () => {}, voiceoverLabel),
    [tokens, voiceoverLabel],
  );

  const unknownMentions = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const tk of tokens) {
      if (tk.kind === "mention" && tk.assetKind === "unknown" && !seen.has(tk.name)) {
        seen.add(tk.name);
        out.push(tk.name);
      }
    }
    return out;
  }, [tokens]);

  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerQuery, setPickerQuery] = useState("");
  const [activeOptionId, setActiveOptionId] = useState<string | null>(null);
  // atStart 既决定 caret anchor 何时插入（picker 定位用），又在 picker-select 时
  // 定位 @ 插入点。用 state 以便 re-render 时 pre 能在正确位置插 caret anchor。
  const [atStart, setAtStart] = useState<number | null>(null);

  const candidates: Record<AssetKind, MentionCandidate[]> = useMemo(() => {
    const buckets: Record<Exclude<AssetKind, "character">, Record<string, unknown> | undefined> = {
      product: project?.products,
      scene: project?.scenes,
      prop: project?.props,
    };
    const out = {} as Record<AssetKind, MentionCandidate[]>;
    for (const kind of ["product", "scene", "prop"] as const) {
      const bucket = buckets[kind];
      out[kind] = Object.entries(bucket ?? {}).map(([name, data]) => ({
        name,
        imagePath: (data as Partial<Record<(typeof SHEET_FIELD)[AssetKind], string>>)[SHEET_FIELD[kind]] ?? null,
      }));
    }
    // 角色候选含衍生：`本体名/衍生名` 是独立的引用名，取的是该形态自己的资产图。
    out.character = characterReferenceForms(project?.characters).map((form) => ({
      name: form.name,
      imagePath: form.asset.character_sheet ?? null,
    }));
    return out;
  }, [project?.products, project?.characters, project?.scenes, project?.props]);

  const updatePickerFromCursor = useCallback((nextValue: string, cursor: number) => {
    // 向左扫描寻找 @ 触发符。旧格式只允许 `\w` + CJK + `/` 作为正在输入的 query；
    // 包裹格式 `@[query` 则允许标点参与过滤，直到遇到空白或闭合括号。
    // `/` 是衍生引用名的分隔符（`本体/衍生`）：不放行它，键入 `@阿岚/` 会当场关掉候选列表，
    // 衍生就只能靠包裹形态或整段搜索才选得到。插入的仍是包裹形态 `@[本体/衍生]`。
    let i = cursor - 1;
    while (i >= 0) {
      const ch = nextValue[i];
      if (ch === "@") {
        const prev = nextValue[i - 1];
        // 与 MENTION_RE `(?<!\w)` 对齐：@ 左侧不能是 ASCII 词字符，否则视为 email/id 残片。
        if (i === 0 || !/\w/.test(prev ?? "")) {
          const rawQuery = nextValue.slice(i + 1, cursor);
          const isWrapped = rawQuery.startsWith("[");
          if (!isWrapped && !/^[\w一-鿿/]*$/.test(rawQuery)) break;
          setAtStart(i);
          setPickerQuery(isWrapped ? rawQuery.slice(1) : rawQuery);
          setPickerOpen(true);
          return;
        }
        break;
      }
      if (ch === "]" || /\s/.test(ch)) break;
      i--;
    }
    setAtStart(null);
    setPickerOpen(false);
    setPickerQuery("");
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const next = e.target.value;
    onChange(next);
    updatePickerFromCursor(next, e.target.selectionStart ?? next.length);
  };

  const handleCursorUpdate = (e: React.SyntheticEvent<HTMLTextAreaElement>) => {
    const ta = e.currentTarget;
    updatePickerFromCursor(ta.value, ta.selectionStart ?? ta.value.length);
  };

  const handleTextareaBlur = useCallback(() => {
    // Picker options call `e.preventDefault()` on mousedown, so the textarea
    // retains focus through the click and this handler only fires on genuine
    // "focus left the editor" transitions — safe to close synchronously.
    setPickerOpen(false);
    setPickerQuery("");
    setAtStart(null);
    setActiveOptionId(null);
  }, []);

  // Backspace 两次删除：当光标紧挨在一个完整 @mention 的末尾且无选区时，
  // 第一次退格仅选中该 mention（让用户看到高亮），默认行为不删除；第二次按下时
  // selectionStart !== selectionEnd，浏览器默认就会删除整个选区。
  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Backspace") return;
    const ta = e.currentTarget;
    const start = ta.selectionStart ?? 0;
    const end = ta.selectionEnd ?? 0;
    if (start !== end) return;
    const text = ta.value;
    // 向左扫到最近的 @，用 MENTION_RE 判断它是否是一个完整 mention。
    // 限制扫描范围（光标前 64 字符）避免长文本里 O(n) 扫每次 backspace。
    const scanFrom = Math.max(0, start - 64);
    const slice = text.slice(scanFrom, start);
    for (const m of slice.matchAll(MENTION_RE)) {
      const localIdx = m.index ?? 0;
      const absoluteStart = scanFrom + localIdx;
      const absoluteEnd = absoluteStart + m[0].length;
      if (absoluteEnd === start) {
        e.preventDefault();
        ta.setSelectionRange(absoluteStart, absoluteEnd);
        return;
      }
    }
  }, []);

  const handlePickerSelect = useCallback(
    (ref: { type: AssetKind; name: string }) => {
      const ta = taRef.current;
      const start = atStart;
      if (!ta || start === null) {
        setPickerOpen(false);
        return;
      }
      const before = currentText.slice(0, start);
      const cursor = ta.selectionStart ?? currentText.length;
      const after = currentText.slice(cursor);
      const insert = `@[${ref.name}] `;
      const next = before + insert + after;
      onChange(next);
      setPickerOpen(false);
      setPickerQuery("");
      setAtStart(null);
      setActiveOptionId(null);
      requestAnimationFrame(() => {
        ta.focus();
        const pos = before.length + insert.length;
        ta.setSelectionRange(pos, pos);
      });
    },
    [currentText, atStart, onChange],
  );

  const onScroll = () => {
    if (preRef.current && taRef.current) {
      preRef.current.scrollTop = taRef.current.scrollTop;
      preRef.current.scrollLeft = taRef.current.scrollLeft;
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-1 flex items-center justify-between text-[11px] text-gray-500">
        <span className="font-mono text-gray-400" translate="no">
          {unit.unit_id}
        </span>
        <span className="tabular-nums text-gray-500">
          {t("reference_editor_unit_meta", { duration: unit.duration_seconds })}
        </span>
      </div>

      <div className="relative min-h-0 flex-1 rounded-md border border-gray-800 bg-gray-950/60">
        <pre
          ref={preRef}
          aria-hidden
          className="pointer-events-none absolute inset-0 m-0 overflow-hidden whitespace-pre-wrap break-words p-3 font-mono text-sm leading-6"
        >
          {pickerOpen
            ? renderHighlightedTokens(tokens, atStart, setAnchorEl, voiceoverLabel)
            : staticHighlightedNodes}
          {currentText.endsWith("\n") ? "\u200b" : null}
        </pre>

        <textarea
          ref={taRef}
          value={currentText}
          onChange={handleChange}
          onKeyUp={handleCursorUpdate}
          onKeyDown={handleKeyDown}
          onClick={handleCursorUpdate}
          onBlur={handleTextareaBlur}
          onScroll={onScroll}
          role="combobox"
          aria-expanded={pickerOpen}
          aria-controls={MENTION_PICKER_DEFAULT_ID}
          aria-autocomplete="list"
          aria-activedescendant={pickerOpen && activeOptionId ? activeOptionId : undefined}
          aria-describedby={unknownMentions.length > 0 ? "reference-editor-unknown-desc" : undefined}
          placeholder={t("reference_editor_placeholder")}
          aria-label={t("reference_editor_aria_name")}
          spellCheck={false}
          className="absolute inset-0 h-full w-full resize-none bg-transparent p-3 font-mono text-sm leading-6 text-transparent caret-gray-200 placeholder:text-gray-600 focus:outline-none"
        />

        {pickerOpen && anchorEl && (
          <MentionPicker
            open
            query={pickerQuery}
            candidates={candidates}
            projectName={projectName}
            anchorElement={anchorEl}
            onSelect={handlePickerSelect}
            onClose={() => {
              setPickerOpen(false);
              setPickerQuery("");
              setAtStart(null);
              setActiveOptionId(null);
            }}
            onActiveChange={setActiveOptionId}
          />
        )}
      </div>

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
    </div>
  );
}
