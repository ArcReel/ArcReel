import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2 } from "lucide-react";
import { API } from "@/api";
import { ScriptHighlight } from "@/components/shared/ScriptHighlight";
import { assetColor } from "./asset-colors";
import type { MentionLookup } from "@/hooks/useUnitPromptHighlight";
import { errMsg } from "@/utils/async";
import type { ScriptPreview } from "@/types";

/** Wait after typing before parse requests, in ms; parsing is read-only, throttled only to reduce round trips. */
const DEBOUNCE_MS = 400;

export interface ScriptPreviewPanelProps {
  projectName: string;
  episode: number;
  /** Current unit body, preferring drafts, matching the editor. */
  text: string;
  /** Asset name → type for mention coloring; callers must memoize. */
  lookup: MentionLookup;
}

/**
 * Parse preview: show editor bodies as interpreted by parsers.
 *
 * Local highlighting responds immediately to typing; derived dialogue/fallback hints come from backend parsing
 * (voice-related ones depend on current project video capabilities, unknowable frontend-side), requested once
 * typing stops. Next keystrokes immediately abort preceding requests via AbortSignal, preventing stale-response overwrite.
 *
 * Derived outputs also depend on project assets (unregistered mentions/speakers/missing character reference audio
 * warnings read them), so `lookup` changes refetch too, avoiding old notices after asset edits.
 *
 * Caller tabpanels own scrolling/keyboard focus: read-only panels have no focusable descendants, and WAI tabs
 * convention gives tabpanel `tabindex="0"`. Other parent containers must provide `overflow-y-auto` +
 * `tabIndex={0}`, preventing long-text overflow and keyboard-inaccessible content below the fold.
 */
export function ScriptPreviewPanel({ projectName, episode, text, lookup }: ScriptPreviewPanelProps) {
  const { t } = useTranslation("dashboard");
  const [preview, setPreview] = useState<ScriptPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Inputs that produced this preview. Mismatches mean stale panels during throttle waiting plus
  // requests in flight. Do not clear on every keystroke and flash empty; mark stale instead:
  // reduce opacity/set aria-busy so readers do not mistake old dialogue/notices for current-body results.
  const [appliedFor, setAppliedFor] = useState<{
    projectName: string;
    episode: number;
    text: string;
    lookup: MentionLookup;
  } | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      const controller = new AbortController();
      controllerRef.current = controller;
      setLoading(true);
      API.previewReferenceScript(projectName, episode, text, { signal: controller.signal })
        .then((result) => {
          if (controller.signal.aborted) return;
          setPreview(result);
          setError(null);
          setAppliedFor({ projectName, episode, text, lookup });
        })
        .catch((e: unknown) => {
          if (controller.signal.aborted) return;
          // Also clear prior results: changed bodies with old derived output would otherwise leave
          // mismatched dialogue beneath error banners.
          setPreview(null);
          setError(errMsg(e));
          setAppliedFor(null);
        })
        .finally(() => {
          if (controller.signal.aborted) return;
          setLoading(false);
        });
    }, DEBOUNCE_MS);
    // Invalidate requests immediately on edits, not at next debounce/controller replacement;
    // otherwise old results may return during these 400ms, populating mismatched output.
    return () => {
      clearTimeout(timer);
      controllerRef.current?.abort();
    };
  }, [projectName, episode, text, lookup]);

  const utterances = useMemo(() => preview?.utterances ?? [], [preview]);
  const counts = useMemo(
    () => ({
      dialogue: utterances.filter((u) => u.kind === "dialogue").length,
      voiceover: utterances.filter((u) => u.kind === "voiceover").length,
    }),
    [utterances],
  );

  const warnings = preview?.warnings ?? [];
  const stale =
    appliedFor !== null &&
    (appliedFor.projectName !== projectName ||
      appliedFor.episode !== episode ||
      appliedFor.text !== text ||
      appliedFor.lookup !== lookup);

  return (
    <div className="flex min-h-0 flex-1 flex-col p-3">
      <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
        <span>{t("script_preview_hint")}</span>
        <span className="flex-1" />
        {(stale || loading) && (
          <span role="status" className="inline-flex items-center">
            <Loader2 className="size-3 animate-spin" aria-hidden="true" />
            <span className="sr-only">{t("script_preview_loading")}</span>
          </span>
        )}
      </div>

      {error && (
        <p role="alert" className="mb-2 rounded-md bg-destructive/10 px-2.5 py-1.5 text-xs text-destructive">
          {t("script_preview_failed", { error })}
        </p>
      )}

      {warnings.length > 0 && (
        <ul
          aria-label={t("script_preview_warnings_label")}
          aria-live="polite"
          aria-busy={stale || undefined}
          className={`mb-3 flex flex-col gap-1 rounded-md border border-warn/30 bg-warn/10 p-2 ${
            stale ? "opacity-45" : ""
          }`}
        >
          {warnings.map((w, i) => (
            // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Warnings are read-only text derived as a whole list from bodies; keys may repeat and rows have no state
            <li key={`${w.key}-${i}`} className="flex gap-1.5 text-xs leading-relaxed text-warn">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
              <span>{w.message}</span>
            </li>
          ))}
        </ul>
      )}

      <ScriptHighlight
        text={text}
        lookup={lookup}
        className="rounded-md border border-border/50 bg-background/60 p-3"
      />

      <dl
        aria-busy={stale || undefined}
        className={`mt-3 grid grid-cols-[auto_1fr] gap-x-3.5 gap-y-2 border-t border-border/50 pt-3 text-xs ${
          stale ? "opacity-45" : ""
        }`}
      >
        <dt className="text-muted-foreground">{t("script_preview_utterances")}</dt>
        <dd className="text-subtle-foreground">
          {counts.dialogue + counts.voiceover > 0
            ? t("script_preview_utterances_value", {
                dialogue: counts.dialogue,
                voiceover: counts.voiceover,
              })
            : <span className="text-muted-foreground">{t("script_preview_none")}</span>}
        </dd>
      </dl>

      {/* Backend-derived dialogue contrasts with local highlighting above, revealing any parser-rule mismatch. */}
      {utterances.length > 0 && (
        <ul
          aria-busy={stale || undefined}
          className={`mt-2 flex flex-col gap-1 text-xs ${stale ? "opacity-45" : ""}`}
        >
          {utterances.map((u) => {
            const palette = assetColor(u.kind === "dialogue" ? "character" : "unknown");
            return (
              <li key={u.index} className="flex items-baseline gap-2">
                <span className="shrink-0 font-mono tabular-nums text-muted-foreground">
                  {t("script_preview_utterance_badge", { index: u.index })}
                </span>
                <span
                  translate="no"
                  className={`shrink-0 rounded-sm px-1 ${palette.textClass} ${palette.bgClass}`}
                >
                  {u.kind === "dialogue" ? u.speaker : t("script_highlight_voiceover")}
                </span>
                <span className="min-w-0 flex-1 break-words text-subtle-foreground">{u.text}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
