import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { H3PromptState } from "@/types";

interface H3PromptPanelProps {
  state: H3PromptState | null;
  loading: boolean;
  optimizing: boolean;
  error: string | null;
}

export function H3PromptPanel({ state, loading, optimizing, error }: H3PromptPanelProps) {
  const { t } = useTranslation("dashboard");
  const renderedPrompt = state?.artifact?.rendered_prompt;
  const emptyMessage = optimizing
    ? t("h3_prompt_auto_running")
    : state?.state === "missing" || state?.state === "stale"
      ? t("h3_prompt_auto_pending")
      : t("h3_prompt_empty");

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-3">
      <div className="mb-2 flex items-center gap-2 px-1">
        <p className="text-[11px] text-[var(--color-text-3)]">
          {renderedPrompt ? t("h3_prompt_ready_description") : t("h3_prompt_description")}
        </p>
        {loading && <Loader2 className="h-3 w-3 animate-spin text-[var(--color-text-4)]" aria-hidden="true" />}
      </div>
      {error ? (
        <p role="alert" className="rounded-md border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-300">
          {error}
        </p>
      ) : (
        <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap rounded-lg border border-[var(--color-hairline)] bg-black/20 p-4 text-xs leading-6 text-[var(--color-text-2)]">
          {renderedPrompt || emptyMessage}
        </pre>
      )}
    </div>
  );
}
