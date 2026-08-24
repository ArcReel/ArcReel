import { Loader2, Pencil, Save } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { H3PromptState } from "@/types";

interface H3PromptPanelProps {
  state: H3PromptState | null;
  loading: boolean;
  optimizing: boolean;
  error: string | null;
  editing: boolean;
  draft: string;
  dirty: boolean;
  saving: boolean;
  onEdit: () => void;
  onChange: (value: string) => void;
  onSave: () => void;
}

export function H3PromptPanel({
  state,
  loading,
  optimizing,
  error,
  editing,
  draft,
  dirty,
  saving,
  onEdit,
  onChange,
  onSave,
}: H3PromptPanelProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const renderedPrompt = state?.artifact?.rendered_prompt;
  const canEdit = Boolean(renderedPrompt) && (state?.state === "pending_review" || state?.state === "confirmed");
  const emptyMessage = optimizing
    ? t("h3_prompt_auto_running")
    : state?.state === "missing" || state?.state === "stale"
      ? t("h3_prompt_auto_pending")
      : t("h3_prompt_empty");

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3">
        <p className="text-[11px] text-[var(--color-text-3)]">
          {renderedPrompt ? t("h3_prompt_ready_description") : t("h3_prompt_description")}
        </p>
        {loading && <Loader2 className="h-3 w-3 animate-spin text-[var(--color-text-4)]" aria-hidden="true" />}
        <span className="flex-1" />
        {!editing && canEdit && (
          <button
            type="button"
            onClick={onEdit}
            disabled={loading || optimizing}
            aria-label={t("common:edit")}
            title={t("common:edit")}
            className="focus-ring rounded-[6px] p-1 text-[var(--color-text-4)] transition-colors hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        )}
      </div>
      {error && (
        <p role="alert" className="mx-3 mb-3 rounded-md border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-300">
          {error}
        </p>
      )}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-3 pb-3">
        {editing ? (
          <textarea
            value={draft}
            onChange={(event) => onChange(event.target.value)}
            readOnly={saving}
            aria-busy={saving}
            aria-label={t("h3_prompt_edit_label")}
            className="focus-ring min-h-0 flex-1 resize-none rounded-lg border border-[var(--color-hairline)] bg-black/20 p-4 font-mono text-xs leading-6 text-[var(--color-text)] outline-none"
          />
        ) : (
          <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap rounded-lg border border-[var(--color-hairline)] bg-black/20 p-4 text-xs leading-6 text-[var(--color-text-2)]">
            {renderedPrompt || emptyMessage}
          </pre>
        )}
      </div>
      {editing && (
        <div className="flex flex-shrink-0 items-center gap-2 border-t border-[var(--color-hairline-soft)] bg-[oklch(0.18_0.010_265_/_0.5)] px-3.5 py-2">
          <span
            className={`inline-flex items-center gap-1.5 text-[11px] ${
              dirty ? "text-amber-300" : "text-[var(--color-text-4)]"
            }`}
          >
            <span
              aria-hidden="true"
              className={`h-1.5 w-1.5 rounded-full ${dirty ? "bg-amber-400" : "bg-emerald-400"}`}
            />
            {dirty ? t("reference_unsaved") : t("reference_synced")}
          </span>
          <span className="flex-1" />
          <button
            type="button"
            onClick={onSave}
            disabled={!dirty || saving}
            className={`focus-ring inline-flex min-w-[80px] items-center justify-center gap-1.5 rounded-md px-3 py-1 text-xs font-semibold ${
              dirty
                ? "text-[oklch(0.14_0_0)] [background:linear-gradient(180deg,var(--color-accent-2),var(--color-accent))] shadow-[inset_0_1px_0_oklch(1_0_0_/_0.3),0_4px_12px_-4px_var(--color-accent-glow)]"
                : "border border-[var(--color-hairline)] bg-[oklch(0.22_0.011_265_/_0.5)] text-[var(--color-text-4)]"
            } disabled:cursor-not-allowed`}
          >
            {saving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Save className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {saving ? t("common:saving") : t("common:save")}
          </button>
        </div>
      )}
    </div>
  );
}
