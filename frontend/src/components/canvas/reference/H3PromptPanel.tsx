import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, Loader2, RefreshCw, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import type { H3PromptState, ReferenceVideoUnit } from "@/types";
import { errMsg } from "@/utils/async";

interface H3PromptPanelProps {
  projectName: string;
  episode: number;
  units: readonly ReferenceVideoUnit[];
  narrationDelivery: "post_production" | "use_tts";
}

const STATE_CLASS: Record<H3PromptState["state"], string> = {
  not_applicable: "text-[var(--color-text-4)]",
  missing: "text-amber-300",
  stale: "text-orange-300",
  pending_review: "text-sky-300",
  confirmed: "text-emerald-300",
};

export function H3PromptPanel({
  projectName,
  episode,
  units,
  narrationDelivery,
}: H3PromptPanelProps) {
  const { t } = useTranslation("dashboard");
  const [states, setStates] = useState<H3PromptState[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(units[0]?.unit_id ?? null);
  const [busy, setBusy] = useState<"load" | "optimize" | "confirm" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const request = useMemo(
    () => ({ narration_delivery: narrationDelivery }),
    [narrationDelivery],
  );
  const refresh = useCallback(async () => {
    setBusy("load");
    setError(null);
    try {
      const response = await API.getH3PromptStates(projectName, episode, request);
      setStates(response.states);
      setSelectedId((current) =>
        current && response.states.some((state) => state.unit_id === current)
          ? current
          : (response.states[0]?.unit_id ?? null),
      );
    } catch (cause) {
      setError(errMsg(cause));
    } finally {
      setBusy(null);
    }
  }, [episode, projectName, request]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch lifecycle is owned by this external API sync
    void refresh();
  }, [refresh, units]);

  const selected = states.find((state) => state.unit_id === selectedId) ?? null;
  const operate = useCallback(
    async (operation: "optimize" | "confirm") => {
      if (!selectedId) return;
      setBusy(operation);
      setError(null);
      try {
        const payload = { ...request, unit_ids: [selectedId] };
        if (operation === "optimize") await API.optimizeH3Prompts(projectName, episode, payload);
        else await API.confirmH3Prompts(projectName, episode, payload);
        const response = await API.getH3PromptStates(projectName, episode, request);
        setStates(response.states);
      } catch (cause) {
        setError(errMsg(cause));
      } finally {
        setBusy(null);
      }
    },
    [episode, projectName, request, selectedId],
  );

  return (
    <div className="grid h-full min-h-0 grid-cols-[240px_minmax(0,1fr)]">
      <aside className="min-h-0 overflow-auto border-r border-[var(--color-hairline)] p-3">
        <div className="mb-3 flex items-center justify-between">
          <p className="text-xs font-semibold text-[var(--color-text-2)]">{t("h3_prompt_units")}</p>
          <button type="button" onClick={() => void refresh()} className="focus-ring rounded p-1 text-[var(--color-text-3)]" aria-label={t("h3_prompt_refresh")}>
            <RefreshCw className={`h-3.5 w-3.5 ${busy === "load" ? "animate-spin" : ""}`} />
          </button>
        </div>
        <div className="space-y-1.5">
          {states.map((state) => (
            <button
              key={state.unit_id}
              type="button"
              onClick={() => setSelectedId(state.unit_id)}
              className={`focus-ring flex w-full items-center justify-between rounded-md border px-3 py-2 text-left text-xs ${selectedId === state.unit_id ? "border-[var(--color-accent)]/60 bg-[var(--color-accent-soft)]" : "border-[var(--color-hairline)]"}`}
            >
              <span className="font-mono">{state.unit_id}</span>
              <span className={STATE_CLASS[state.state]}>{t(`h3_prompt_state_${state.state}`)}</span>
            </button>
          ))}
        </div>
      </aside>
      <section className="flex min-h-0 flex-col p-5">
        <div className="mb-4 flex items-start gap-3">
          <div>
            <h3 className="text-sm font-semibold text-[var(--color-text)]">{t("h3_prompt_title")}</h3>
            <p className="mt-1 text-xs text-[var(--color-text-3)]">{t("h3_prompt_description")}</p>
          </div>
          <span className="flex-1" />
          <button type="button" disabled={!selected || busy !== null || selected.state === "not_applicable"} onClick={() => void operate("optimize")} className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-[var(--color-hairline)] px-3 py-1.5 text-xs disabled:opacity-50">
            {busy === "optimize" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
            {t("h3_prompt_optimize")}
          </button>
          <button type="button" disabled={!selected || busy !== null || selected.state !== "pending_review"} onClick={() => void operate("confirm")} className="focus-ring inline-flex items-center gap-1.5 rounded-md bg-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-black disabled:opacity-50">
            {busy === "confirm" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
            {t("h3_prompt_confirm")}
          </button>
        </div>
        {error && <p role="alert" className="mb-3 rounded border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-300">{error}</p>}
        <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap rounded-lg border border-[var(--color-hairline)] bg-black/20 p-4 text-xs leading-6 text-[var(--color-text-2)]">
          {selected?.artifact?.rendered_prompt || t("h3_prompt_empty")}
        </pre>
      </section>
    </div>
  );
}
