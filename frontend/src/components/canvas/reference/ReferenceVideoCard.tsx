import { useCallback, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { MENTION_PICKER_DEFAULT_ID, MentionPicker, type MentionCandidate } from "./MentionPicker";
import { ASSET_COLORS, assetColor } from "./asset-colors";
import { useShotPromptHighlight, type MentionLookup } from "@/hooks/useShotPromptHighlight";
import { mergeReferences } from "@/utils/reference-mentions";
import { useProjectsStore } from "@/stores/projects-store";
import {
  SHEET_FIELD,
  type AssetKind,
  type ReferenceResource,
  type ReferenceVideoUnit,
} from "@/types/reference-video";

export interface ReferenceVideoCardProps {
  unit: ReferenceVideoUnit;
  projectName: string;
  episode: number;
  onChangePrompt: (prompt: string, references: ReferenceResource[]) => void;
}

function unitPromptText(unit: ReferenceVideoUnit): string {
  // Backend `parse_prompt` strips `Shot N (Xs):` headers when persisting
  // shots[].text, so editing the raw stored text would re-parse as a
  // header-less single shot and collapse multi-shot units. Reconstruct the
  // headers unless the unit was saved in header-less mode (duration_override).
  if (unit.duration_override) {
    return unit.shots[0]?.text ?? "";
  }
  return unit.shots
    .map((s, i) => `Shot ${i + 1} (${s.duration}s): ${s.text}`)
    .join("\n");
}

export function ReferenceVideoCard({
  unit,
  projectName: _projectName,
  episode: _episode,
  onChangePrompt,
}: ReferenceVideoCardProps) {
  const { t } = useTranslation("dashboard");
  const taRef = useRef<HTMLTextAreaElement>(null);
  const preRef = useRef<HTMLPreElement>(null);

  // 父层以 key={unit.unit_id} 让 React 自动 remount 本组件，所以这里只持有当前 unit
  // 的本地编辑态；切换 unit 时组件重建，initializer 会重新跑。
  const [currentText, setCurrentText] = useState(() => unitPromptText(unit));

  const project = useProjectsStore((s) => s.currentProjectData);

  const lookup: MentionLookup = useMemo(() => {
    const out: MentionLookup = {};
    for (const name of Object.keys(project?.characters ?? {})) out[name] = "character";
    for (const name of Object.keys(project?.scenes ?? {})) out[name] = "scene";
    for (const name of Object.keys(project?.props ?? {})) out[name] = "prop";
    return out;
  }, [project?.characters, project?.scenes, project?.props]);

  const tokens = useShotPromptHighlight(currentText, lookup);

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
  const atStartRef = useRef<number | null>(null);

  const candidates: Record<AssetKind, MentionCandidate[]> = useMemo(() => {
    const buckets: Record<AssetKind, Record<string, unknown> | undefined> = {
      character: project?.characters,
      scene: project?.scenes,
      prop: project?.props,
    };
    const out = {} as Record<AssetKind, MentionCandidate[]>;
    for (const kind of ["character", "scene", "prop"] as const) {
      const bucket = buckets[kind];
      out[kind] = Object.entries(bucket ?? {}).map(([name, data]) => ({
        name,
        imagePath: (data as Partial<Record<(typeof SHEET_FIELD)[AssetKind], string>>)[SHEET_FIELD[kind]] ?? null,
      }));
    }
    return out;
  }, [project?.characters, project?.scenes, project?.props]);

  const emitChange = useCallback(
    (nextValue: string) => {
      const refs = mergeReferences(nextValue, unit.references, project ?? null);
      onChangePrompt(nextValue, refs);
    },
    [onChangePrompt, unit.references, project],
  );

  const updatePickerFromCursor = useCallback((nextValue: string, cursor: number) => {
    let i = cursor - 1;
    while (i >= 0) {
      const ch = nextValue[i];
      if (ch === "@") {
        const prev = nextValue[i - 1];
        // 与 MENTION_RE `(?<!\w)` 对齐：@ 左侧不能是 ASCII 词字符，否则视为 email/id
        // 残片。中文标点、空白、CJK、行首都满足"非 \w"，不会误拦截。
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

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const next = e.target.value;
    setCurrentText(next);
    emitChange(next);
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
    atStartRef.current = null;
    setActiveOptionId(null);
  }, []);

  const handlePickerSelect = useCallback(
    (ref: { type: AssetKind; name: string }) => {
      const ta = taRef.current;
      const start = atStartRef.current;
      if (!ta || start === null) {
        setPickerOpen(false);
        return;
      }
      const before = currentText.slice(0, start);
      const cursor = ta.selectionStart ?? currentText.length;
      const after = currentText.slice(cursor);
      const insert = `@${ref.name} `;
      const next = before + insert + after;
      setCurrentText(next);
      emitChange(next);
      setPickerOpen(false);
      setPickerQuery("");
      atStartRef.current = null;
      setActiveOptionId(null);
      requestAnimationFrame(() => {
        ta.focus();
        const pos = before.length + insert.length;
        ta.setSelectionRange(pos, pos);
      });
    },
    [currentText, setCurrentText, emitChange],
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
          {t("reference_editor_unit_meta", {
            duration: unit.duration_seconds,
            count: unit.shots.length,
          })}
        </span>
      </div>

      <div className="relative min-h-0 flex-1 rounded-md border border-gray-800 bg-gray-950/60">
        <pre
          ref={preRef}
          aria-hidden
          className="pointer-events-none absolute inset-0 m-0 overflow-hidden whitespace-pre-wrap break-words p-3 font-mono text-sm leading-6"
        >
          {tokens.map((tk, i) => {
            if (tk.kind === "shot_header") {
              return (
                <span key={i} className="font-semibold text-indigo-300">
                  {tk.text}
                </span>
              );
            }
            if (tk.kind === "mention") {
              const palette = assetColor(tk.assetKind);
              return (
                <span key={i} className={`rounded px-0.5 ${palette.textClass} ${palette.bgClass}`}>
                  {tk.text}
                </span>
              );
            }
            return <span key={i}>{tk.text}</span>;
          })}
          {currentText.endsWith("\n") ? "\u200b" : null}
        </pre>

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
          aria-controls={MENTION_PICKER_DEFAULT_ID}
          aria-autocomplete="list"
          aria-activedescendant={pickerOpen && activeOptionId ? activeOptionId : undefined}
          aria-describedby={unknownMentions.length > 0 ? "reference-editor-unknown-desc" : undefined}
          placeholder={t("reference_editor_placeholder")}
          aria-label={t("reference_editor_aria_name")}
          spellCheck={false}
          className="absolute inset-0 h-full w-full resize-none bg-transparent p-3 font-mono text-sm leading-6 text-transparent caret-gray-200 placeholder:text-gray-600 focus:outline-none"
        />

        {pickerOpen && (
          <div className="absolute bottom-1 left-3 z-20">
            <MentionPicker
              open
              query={pickerQuery}
              candidates={candidates}
              onSelect={handlePickerSelect}
              onClose={() => {
                setPickerOpen(false);
                setPickerQuery("");
                atStartRef.current = null;
                setActiveOptionId(null);
              }}
              onActiveChange={setActiveOptionId}
            />
          </div>
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
