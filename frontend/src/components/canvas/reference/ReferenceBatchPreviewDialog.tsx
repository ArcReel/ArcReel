import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { API } from "@/api";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { BatchAdmissionSummary } from "@/components/workflow/BatchAdmissionSummary";
import { EpisodeItemRef } from "@/components/shared/episode-item/EpisodeItemRef";
import { errMsg } from "@/utils/async";
import { costEntries, formatCurrencyAmount } from "@/utils/cost-format";
import type { ReferenceBatchPreview } from "@/types";

/** No final video but excluded from this batch: local canvas writes in flight, or another submission finished video before this one. */
export type ReferenceBatchSkipReason = "has_video" | "local_write";

export interface ReferenceBatchSkip {
  unit_id: string;
  reason: ReferenceBatchSkipReason;
}

/**
 * Reference-to-video fill-videos confirmation: list target units, skips/reasons, and estimated costs when computable.
 *
 * Membership/conclusions come from previews sharing submission admission. When duration tiers need confirmation, display tier groups here;
 * primary action submits those tiers without another confirmation layer. Blocked previews state every gap without submission.
 * Ignore closing while submitting; canvas handles submission outcomes.
 */
export function ReferenceBatchPreviewDialog({
  projectName,
  episode,
  unitIds,
  skipped,
  onConfirm,
  onClose,
}: {
  projectName: string;
  episode: number;
  unitIds: string[];
  /** Units removed by canvas before submission; preview adds server-skipped units already having final videos. */
  skipped: ReferenceBatchSkip[];
  onConfirm: (preview: ReferenceBatchPreview) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [preview, setPreview] = useState<ReferenceBatchPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    API.previewReferenceVideoBatch(projectName, episode, { unit_ids: unitIds }, { signal: controller.signal })
      .then((res) => {
        if (!controller.signal.aborted) setPreview(res);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errMsg(err));
      });
    return () => controller.abort();
  }, [projectName, episode, unitIds]);

  const blocked = preview?.decision === "blocked";
  const confirming = preview?.decision === "confirmation_required";
  const targets = preview?.units ?? [];

  const skippedIds = new Set(skipped.map((item) => item.unit_id));
  const allSkipped: ReferenceBatchSkip[] = [
    ...skipped,
    ...(preview?.skipped_unit_ids ?? [])
      .filter((unitId) => !skippedIds.has(unitId))
      .map((unitId) => ({ unit_id: unitId, reason: "has_video" as const })),
  ];

  const cost = costEntries(preview?.estimated_cost ?? undefined)
    .map(([currency, amount]) => formatCurrencyAmount(currency, amount))
    .join(" + ");

  const handleConfirm = async () => {
    if (!preview) return;
    setSubmitting(true);
    try {
      await onConfirm(preview);
    } finally {
      setSubmitting(false);
    }
  };

  let body;
  if (error) {
    body = <p role="alert">{t("reference_batch_preview_load_failed", { message: error })}</p>;
  } else if (!preview) {
    body = <p>{t("storyboard_batch_loading")}</p>;
  } else {
    body = (
      <div className="flex flex-col gap-3" data-testid="reference-batch-preview">
        {targets.length === 0 ? (
          <p>{t("reference_batch_preview_nothing")}</p>
        ) : blocked ? (
          // When blocked, list all units' gaps and collaterally held units; this already provides membership.
          <BatchAdmissionSummary admission={preview} />
        ) : (
          <>
            <div className="flex flex-col gap-1">
              <p className="font-medium text-subtle-foreground">
                {t("reference_batch_preview_targets", { count: targets.length })}
              </p>
              <div className="flex flex-wrap gap-x-3 gap-y-1.5">
                {targets.map((unit) => (
                  <EpisodeItemRef key={unit.unit_id} itemId={unit.unit_id} />
                ))}
              </div>
            </div>
            {/* Tier groups include only units needing confirmation; separately list all units because others submit too. */}
            {confirming && <BatchAdmissionSummary admission={preview} />}
          </>
        )}
        {allSkipped.length > 0 && (
          <div>
            <p className="font-medium text-subtle-foreground">
              {t("reference_batch_preview_skipped", { count: allSkipped.length })}
            </p>
            <ul className="mt-1 flex flex-col gap-1">
              {allSkipped.map((item) => (
                <li key={item.unit_id} className="flex flex-wrap items-center gap-x-1.5">
                  <EpisodeItemRef itemId={item.unit_id} />
                  <span>{t(`reference_batch_preview_skip.${item.reason}`)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {!blocked && targets.length > 0 && (
          <>
            <p>{cost ? t("storyboard_batch_cost", { cost }) : t("storyboard_batch_cost_unknown")}</p>
            {preview.unpriced_count > 0 && (
              <p>{t("reference_batch_preview_unpriced", { count: preview.unpriced_count })}</p>
            )}
          </>
        )}
      </div>
    );
  }

  const title = t("batch_generate_videos");
  return (
    <AlertDialog
      open
      onOpenChange={(next) => {
        if (!next && !submitting) onClose();
      }}
    >
      <AlertDialogContent size="lg">
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
        </AlertDialogHeader>
        {/* Membership/skipped lists may be long; enable keyboard scrolling in the body. */}
        <AlertDialogBody tabIndex={0} role="region" aria-label={title}>
          <AlertDialogDescription render={<div />}>{body}</AlertDialogDescription>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={submitting}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction
            disabled={submitting || !preview || targets.length === 0 || blocked}
            onClick={() => void handleConfirm()}
          >
            {submitting ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
            {submitting
              ? t("storyboard_batch_submitting")
              : confirming
                ? t("reference_batch_confirm_cta")
                : t("storyboard_batch_confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
