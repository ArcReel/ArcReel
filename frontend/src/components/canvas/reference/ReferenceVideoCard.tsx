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

// Mention pills use inline box-shadow pseudo-borders/background without width; previous `px-0.5`
// added 4px per mention versus textarea text, offsetting cursors from visible characters.
// Classes affect color/background/radius only, never box-model width.
const MENTION_SPAN_CLASS = "rounded-sm";

// Highlight mirrors/inputs require pixel-identical layout: preflight gives pre monospace, textarea `font: inherit`;
// explicitly use proportional body fonts on both or differing widths offset cursors/selections. Input scrollbars
// also narrow available width/change wrapping, so reserve scrollbar gutters on both layers.
const EDITOR_LAYER_CLASS = "p-3 font-sans text-sm leading-6 [scrollbar-gutter:stable]";

/**
 * Render pre-layer token spans; when caretOffset hits token interiors/boundaries, split there
 * and insert zero-size caret anchors for picker positioning.
 *
 * Anchors are used only with pickerOpen; null from callers skips insertion. Avoid inline-layout impact
 * through `w-0 h-[1em] inline-block align-baseline`.
 *
 * Callback refs return anchors to callers, then floating-ui uses portal positioning.
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
      // Match preview `ScriptHighlight`: backgrounds mark parsed dialogue, speaker colors follow
      // asset categories (voiceover has no speaker, always unknown). Preserve source verbatim, adding only colors/titles.
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
      // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Body highlight spans are positional, read-only/stateless; position identifies them
      out.push(<Fragment key={`pre-${i}`}>{renderPiece(tk, tk.text.slice(0, local), `pre-${i}`)}</Fragment>);
      if (anchorEl) out.push(anchorEl);
      // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Same for the post-cursor highlight portion
      out.push(<Fragment key={`post-${i}`}>{renderPiece(tk, tk.text.slice(local), `post-${i}`)}</Fragment>);
      inserted = true;
    } else {
      // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Same for complete highlight spans
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
  beforePreview?: () => Promise<boolean>;
  dirty?: boolean;
  saving?: boolean;
}

/** Prompt-preview reference image, individually subscribed to fingerprints for regenerated URL versions. */
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
  // Callback refs convert caret anchors to state for MentionPicker floating-ui
  // reference elements. Use state rather than refs: floating-ui `refs.setReference` must
  // observe reference changes (initial null → mounted span), and state changes trigger
  // Picker effects to call setReference again.
  const [anchorEl, setAnchorEl] = useState<HTMLSpanElement | null>(null);

  // Parents control prompt values, either server originals or unsaved drafts.
  // Local state holds only MentionPicker UI details.
  const currentText = value;

  const project = useProjectsStore((s) => s.currentProjectData);

  const lookup = useMemo(() => buildMentionLookup(project), [project]);

  const tokens = useUnitPromptHighlight(currentText, lookup);

  const voiceoverLabel = t("script_highlight_voiceover");

  // Most paths have pickerOpen=false; typing briefly opens pickers only on @.
  // useUnitPromptHighlight memoizes tokens; additionally cache tokens→ReactNode lists here,
  // avoiding renderHighlightedTokens forEach reruns on parent/other-state rerenders.
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
  // atStart controls caret-anchor insertion for positioning and locates @ insertion on picker selection.
  // Use state so rerendered pre places caret anchors correctly.
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
    // Character candidates include derivatives: `本体名/衍生名` is an independent reference using that appearance's own sheet.
    out.character = characterReferenceForms(project?.characters).map((form) => ({
      name: form.name,
      imagePath: form.asset.character_sheet ?? null,
    }));
    return out;
  }, [project?.products, project?.characters, project?.scenes, project?.props]);

  const updatePickerFromCursor = useCallback((nextValue: string, cursor: number) => {
    // Scan left for @. Legacy queries allow only `\w` + CJK + `/`;
    // wrapped `@[query` permits punctuation until whitespace/closing brackets.
    // `/` separates derivative names (`本体/衍生`); disallowing it closes candidates on typing `@阿岚/`,
    // limiting derivative selection to wrapped names/full searches. Insert wrapped `@[本体/衍生]` regardless.
    let i = cursor - 1;
    while (i >= 0) {
      const ch = nextValue[i];
      if (ch === "@") {
        const prev = nextValue[i - 1];
        // Match MENTION_RE `(?<!\w)`: ASCII word characters before @ mean email/ID fragments, not mentions.
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

  // Two-step Backspace when cursor directly follows complete @mentions without selection:
  // first selects/highlights the mention without default deletion; second sees
  // selectionStart !== selectionEnd, so browser defaults delete the whole selection.
  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Backspace") return;
    const ta = e.currentTarget;
    const start = ta.selectionStart ?? 0;
    const end = ta.selectionEnd ?? 0;
    if (start !== end) return;
    const text = ta.value;
    // Scan left to nearest @, checking complete mentions with MENTION_RE.
    // Limit scans to 64 preceding characters, avoiding O(n) backspace scans in long text.
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
                      // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Preview results replace whole read-only lists; references may repeat and rows are stateless
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

      {/* Editor fills column height, at least 12rem in short columns with outer scrolling. */}
      <div className="relative min-h-48 flex-1 rounded-md border border-input bg-background/60 transition-colors duration-fast focus-within:border-ring">
        <pre
          ref={preRef}
          aria-hidden
          // Highlight mirrors display only the input-synchronized scroll window; inputs themselves scroll overflow.
          data-overflow-ok="与输入框同步滚动的着色镜像层"
          className={`pointer-events-none absolute inset-0 m-0 overflow-hidden whitespace-pre-wrap break-words ${EDITOR_LAYER_CLASS}`}
        >
          {pickerOpen
            ? renderHighlightedTokens(tokens, atStart, setAnchorEl, voiceoverLabel)
            : staticHighlightedNodes}
          {currentText.endsWith("\n") ? "\u200b" : null}
        </pre>

        {/* eslint-disable-next-line no-restricted-syntax -- Align verbatim with highlight mirrors and synchronize scrolling; Textarea auto-height would break alignment */}
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
