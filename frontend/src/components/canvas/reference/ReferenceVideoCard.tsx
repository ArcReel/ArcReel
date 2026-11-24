import { Fragment, useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { MENTION_PICKER_DEFAULT_ID, MentionPicker, type MentionCandidate } from "./MentionPicker";
import { ASSET_COLORS, assetColor } from "./asset-colors";
import { useUnitPromptHighlight, type Token } from "@/hooks/useUnitPromptHighlight";
import { buildMentionLookup, characterReferenceForms, MENTION_RE } from "@/utils/reference-mentions";
import { useProjectsStore } from "@/stores/projects-store";
import { API } from "@/api";
import { PromptPreviewButton } from "@/components/shared/PromptPreviewButton";
import { SourceTextReadonly } from "@/components/shared/SourceTextReadonly";
import {
  SHEET_FIELD,
  type AssetKind,
  type ReferenceVideoUnit,
} from "@/types/reference-video";

const MENTION_SPAN_CLASS = "rounded-sm";

const EDITOR_LAYER_CLASS = "p-3 font-sans text-sm leading-6 [scrollbar-gutter:stable]";

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
      const palette = assetColor(tk.speakerKind);
      return (
        <span
          key={key}
          className={`${MENTION_SPAN_CLASS} bg-foreground/5 ${tk.speaker ? palette.textClass : ""}`}
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
      // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- 正文按位置切出的着色片段，只读、无状态，位置即身份
      out.push(<Fragment key={`pre-${i}`}>{renderPiece(tk, tk.text.slice(0, local), `pre-${i}`)}</Fragment>);
      if (anchorEl) out.push(anchorEl);
      // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- 同上：光标后半段的着色片段
      out.push(<Fragment key={`post-${i}`}>{renderPiece(tk, tk.text.slice(local), `post-${i}`)}</Fragment>);
      inserted = true;
    } else {
      // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- 同上：完整的着色片段
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
  value: string;
  onChange: (next: string) => void;
  beforePreview?: () => Promise<boolean>;
  dirty?: boolean;
  saving?: boolean;
}

function ReferencePreviewImage({ projectName, path, name }: { projectName: string; path: string; name: string }) {
  const fingerprint = useProjectsStore((s) => s.getAssetFingerprint(path));
  return (
    <img
      src={API.getFileUrl(projectName, path, fingerprint, { width: 160 })}
      alt={name}
      className="h-12 w-16 rounded-sm object-contain"
    />
  );
}

export function ReferenceVideoCard({
  unit,
  projectName,
  episode,
  value,
  onChange,
  beforePreview,
  dirty,
  saving,
}: ReferenceVideoCardProps) {
  const { t } = useTranslation("dashboard");
  const taRef = useRef<HTMLTextAreaElement>(null);
  const preRef = useRef<HTMLPreElement>(null);
  const [anchorEl, setAnchorEl] = useState<HTMLSpanElement | null>(null);

  const currentText = value;

  const project = useProjectsStore((s) => s.currentProjectData);

  const lookup = useMemo(() => buildMentionLookup(project), [project]);

  const tokens = useUnitPromptHighlight(currentText, lookup);

  const voiceoverLabel = t("script_highlight_voiceover");

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
    out.character = characterReferenceForms(project?.characters).map((form) => ({
      name: form.name,
      imagePath: form.asset.character_sheet ?? null,
    }));
    return out;
  }, [project?.products, project?.characters, project?.scenes, project?.props]);

  const updatePickerFromCursor = useCallback((nextValue: string, cursor: number) => {
    let i = cursor - 1;
    while (i >= 0) {
      const ch = nextValue[i];
      if (ch === "@") {
        const prev = nextValue[i - 1];
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
    setPickerOpen(false);
    setPickerQuery("");
    setAtStart(null);
    setActiveOptionId(null);
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Backspace") return;
    const ta = e.currentTarget;
    const start = ta.selectionStart ?? 0;
    const end = ta.selectionEnd ?? 0;
    if (start !== end) return;
    const text = ta.value;
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
    <div className="flex flex-1 flex-col">
      <div className="mb-1 flex items-center justify-end text-xs text-muted-foreground">
        <div className="flex items-center gap-2">
          <PromptPreviewButton
            title={t("reference_prompt_preview_title")}
            beforeOpen={beforePreview}
            saveFirst={dirty}
            disabled={saving}
            load={(signal) => API.previewReferenceUnitPrompt(projectName, episode, unit.unit_id, currentText, { signal })}
            renderExtra={(result) => result.text ? (
              <section aria-label={t("reference_prompt_preview_images")} className="mt-2">
                <h3 className="mb-2 text-xs text-muted-foreground">{t("reference_prompt_preview_images")}</h3>
                {result.references.length ? (
                  <ol className="space-y-2">
                    {result.references.map((reference, index) => (
                      // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- 预览结果整份替换、只读，同一张参考图可出现多次，行内无状态
                      <li key={`${reference.path}-${index}`} className="flex items-center gap-3 text-xs text-subtle-foreground">
                        <ReferencePreviewImage projectName={projectName} path={reference.path} name={reference.name} />
                        <span>{t("reference_prompt_preview_image", { index: index + 1, name: reference.name })}</span>
                      </li>
                    ))}
                  </ol>
                ) : <p className="text-xs text-muted-foreground">{t("reference_prompt_preview_no_images")}</p>}
              </section>
            ) : null}
          />
          <span className="tabular-nums">
            {t("reference_editor_unit_meta", { duration: unit.duration_seconds })}
          </span>
        </div>
      </div>

      {unit.pending_authoring === true && (
        <div role="status" className="mb-2 shrink-0 rounded-lg border border-border/50 bg-warn/5 px-3 py-2 text-xs text-subtle-foreground">
          {t("reference_editor_pending_authoring_hint")}
        </div>
      )}

      <div className="relative min-h-48 flex-1 rounded-md border border-input bg-background/60 transition-colors duration-fast focus-within:border-ring">
        <pre
          ref={preRef}
          aria-hidden
          data-overflow-ok="与输入框同步滚动的着色镜像层"
          className={`pointer-events-none absolute inset-0 m-0 overflow-hidden whitespace-pre-wrap break-words ${EDITOR_LAYER_CLASS}`}
        >
          {pickerOpen
            ? renderHighlightedTokens(tokens, atStart, setAnchorEl, voiceoverLabel)
            : staticHighlightedNodes}
          {currentText.endsWith("\n") ? "\u200b" : null}
        </pre>

        {/* eslint-disable-next-line no-restricted-syntax -- 与着色镜像层逐字对齐、同步滚动，Textarea 的自动撑高会破坏对齐 */}
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
          className={`absolute inset-0 h-full w-full resize-none bg-transparent ${EDITOR_LAYER_CLASS} text-transparent caret-foreground placeholder:text-muted-foreground focus:outline-none`}
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

      <SourceTextReadonly text={unit.source_text} className="mt-3 shrink-0" />

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
                className={`rounded-sm border px-2 py-0.5 text-xs ${palette.textClass} ${palette.bgClass} ${palette.borderClass}`}
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
