import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Bot, Loader2, PenLine, RotateCcw, X } from "lucide-react";
import { usePromptAuthoringSubmit } from "@/hooks/usePromptAuthoringSubmit";
import { PromptOverwriteDialog } from "@/components/canvas/shared/PromptOverwriteDialog";
import { prefillAssistant } from "@/components/shared/prefill-assistant";
import { Button } from "@/components/ui/button";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { useProjectsStore } from "@/stores/projects-store";
import type { ReferenceVideoUnit } from "@/types";
import { episodeAgentRef, itemPositionLabel } from "@/utils/episode-display";
import {
  buildPartialAuthoringAgentPrefill,
  formatPartialAbortReason,
  formatPartialFailureMessage,
  formatReferenceUnitText,
  partialFailureSummary,
  partialAbortSummary,
  referenceUnitPosition,
  type ReferenceAuthoringPartialResult,
} from "./prompt-authoring-partial";

interface PromptAuthoringPartialNoticeProps {
  projectName: string;
  episode: number;
  partial: ReferenceAuthoringPartialResult;
  units: readonly ReferenceVideoUnit[];
  unitSaving?: boolean;
  runWithSave?: (action: () => Promise<boolean>) => Promise<boolean>;
  onEditUnit: (unitId: string) => void;
  onDismiss: () => void;
}

export function PromptAuthoringPartialNotice({
  projectName,
  episode,
  partial,
  units,
  unitSaving = false,
  runWithSave,
  onEditUnit,
  onDismiss,
}: PromptAuthoringPartialNoticeProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const scripts = useProjectsStore((s) => s.currentScripts);
  const episodeLedger = useEpisodeLedger();

  const { busy, submitting, overwrite, enqueue, confirmOverwrite, closeOverwrite } = usePromptAuthoringSubmit(
    projectName,
    episode,
  );
  const [retrying, setRetrying] = useState(false);
  const retryBusy = submitting || retrying || unitSaving;

  const unitsById = useMemo(() => new Map(units.map((unit) => [unit.unit_id, unit])), [units]);
  const retryableUnits = useMemo(
    () =>
      partial.failedUnits
        .map((item) => ({ item, liveUnit: unitsById.get(item.unit_id) ?? null }))
        .filter((entry) => entry.liveUnit !== null),
    [partial.failedUnits, unitsById],
  );
  const retryIds = useMemo(() => retryableUnits.map((entry) => entry.item.unit_id), [retryableUnits]);
  const rewriteMode = useMemo(
    () => partial.rewrite || retryableUnits.some((entry) => entry.liveUnit?.pending_authoring !== true),
    [partial.rewrite, retryableUnits],
  );

  const writtenLabels = useMemo(
    () =>
      partial.writtenIds
        .map((id) => itemPositionLabel("unit", referenceUnitPosition(id, units, scripts), t))
        .join(t("prompt_authoring_id_separator")),
    [partial.writtenIds, units, scripts, t],
  );

  const submitRetry = async (overwriteRevision: string | null) => {
    if (retryBusy) return;
    if (overwriteRevision === null && retryIds.length === 0) return;
    setRetrying(true);
    try {
      const run = () =>
        overwriteRevision !== null
          ? confirmOverwrite(overwriteRevision)
          : enqueue({
              entry_ids: retryIds,
              rewrite: rewriteMode,
              instructions: partial.instructions,
              overwrite_revision: null,
            });
      await (runWithSave ? runWithSave(run) : run());
    } finally {
      setRetrying(false);
    }
  };

  const handToAgent = () => {
    prefillAssistant(
      buildPartialAuthoringAgentPrefill(t, {
        episodeRef: episodeAgentRef(episodeLedger, episode, t),
        units,
        scripts,
        failedUnits: partial.failedUnits,
        rewriteMode,
      }),
    );
  };

  return (
    <>
      <section
        role="status"
        aria-label={t("prompt_authoring_partial_title")}
        className="mx-5 my-2 flex flex-col gap-2.5 rounded-lg border border-warn/40 bg-warn/10 px-4 py-3 text-xs"
      >
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex min-w-0 flex-1 items-start gap-2">
            <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0 text-warn" />
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className="text-sm font-medium text-foreground">{t("prompt_authoring_partial_title")}</span>
                <span className="text-subtle-foreground">
                  {t("prompt_authoring_partial_summary", {
                    writtenCount: partial.writtenIds.length,
                    failedCount: partial.failedUnits.length,
                  })}
                </span>
              </div>
              {writtenLabels && (
                <p className="m-0 text-muted-foreground wrap-break-word">
                  {t("prompt_authoring_partial_written_list", { units: writtenLabels })}
                </p>
              )}
              <p className="m-0 text-muted-foreground">
                {t(rewriteMode ? "prompt_authoring_partial_guide_rewrite" : "prompt_authoring_partial_guide_fill")}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {retryIds.length > 0 && (
              <Button
                variant="outline"
                size="xs"
                disabled={busy || retryBusy}
                title={busy ? t("prompt_authoring_busy") : undefined}
                onClick={() => void submitRetry(null)}
              >
                {retryBusy ? (
                  <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
                ) : (
                  <RotateCcw aria-hidden data-icon="inline-start" />
                )}
                {t(rewriteMode ? "prompt_authoring_partial_retry_rewrite" : "prompt_authoring_partial_retry_failed")}
              </Button>
            )}
            <Button variant="outline" size="xs" onClick={handToAgent}>
              <Bot aria-hidden data-icon="inline-start" />
              {t("prompt_authoring_hand_to_agent")}
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={t("common:close")}
              onClick={onDismiss}
            >
              <X aria-hidden />
            </Button>
          </div>
        </div>

        <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
          {partial.failedUnits.map((item) => {
            const liveUnit = unitsById.get(item.unit_id) ?? null;
            const pos = referenceUnitPosition(item.unit_id, units, scripts);
            const unitLabel = itemPositionLabel("unit", pos, t);
            const isPending = liveUnit?.pending_authoring === true;
            const message = formatPartialFailureMessage(item.message, units, scripts, t);
            const abortReason = formatPartialAbortReason(item.abort_reason, units, scripts, t);
            return (
              <li
                key={item.unit_id}
                className="flex flex-wrap items-start justify-between gap-2 rounded-md border border-border/70 bg-card/80 px-3 py-2"
              >
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-medium text-foreground">{unitLabel}</span>
                    {liveUnit && (
                      <span
                        className={`rounded-sm px-1.5 py-0.5 text-xs font-medium ${
                          isPending ? "bg-warn/15 text-warn" : "bg-muted text-subtle-foreground"
                        }`}
                      >
                        {t(isPending ? "prompt_authoring_entry_pending" : "prompt_authoring_partial_unit_authored")}
                      </span>
                    )}
                  </div>
                  <p className="m-0 text-destructive wrap-break-word">{partialFailureSummary(item, t)}</p>
                  {item.abort_reason && (
                    <p className="m-0 text-subtle-foreground wrap-break-word">
                      {t("prompt_authoring_partial_abort_reason", { reason: partialAbortSummary(item.abort_reason, t) })}
                    </p>
                  )}
                  {(message || abortReason) && (
                    <details className="text-muted-foreground wrap-break-word">
                      <summary>{t("common:error_details")}</summary>
                      {message && <p>{message}</p>}
                      {abortReason && <p>{abortReason}</p>}
                    </details>
                  )}
                </div>
                {liveUnit && (
                  <Button
                    variant="ghost"
                    size="xs"
                    className="shrink-0"
                    onClick={() => onEditUnit(item.unit_id)}
                  >
                    <PenLine aria-hidden data-icon="inline-start" />
                    {t("prompt_authoring_partial_edit_unit")}
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      <PromptOverwriteDialog
        overwrite={overwrite}
        text={overwrite ? formatReferenceUnitText(overwrite.text, units, scripts, t) : null}
        submitting={retryBusy}
        confirmDisabled={busy}
        onConfirm={(revision) => void submitRetry(revision)}
        onClose={closeOverwrite}
      />
    </>
  );
}
