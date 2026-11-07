import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { API } from "@/api";
import { enqueueStoryboardBatch } from "@/actions/generation";
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
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { costEntries, formatCurrencyAmount } from "@/utils/cost-format";
import type { StoryboardBatchKind, StoryboardBatchPreview, WorkflowAdmission } from "@/types";

/**
 * Confirmation for filling an episode's missing storyboard images/videos: list targets, skipped items and reasons, plus estimated costs where available.
 *
 * Whole-batch video admission: if preview already knows admission fails, explain why and disable submission. If state changes after preview and
 * submission is rejected, explain within the dialog too, creating no tasks. Ignore close requests during submission.
 */
export function StoryboardBatchDialog({
  projectName,
  episode,
  kind,
  onClose,
}: {
  projectName: string;
  episode: number;
  kind: StoryboardBatchKind;
  onClose: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [preview, setPreview] = useState<StoryboardBatchPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<WorkflowAdmission | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    API.previewStoryboardBatch(projectName, episode, kind, { signal: controller.signal })
      .then((res) => {
        if (!controller.signal.aborted) setPreview(res);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errMsg(err));
      });
    return () => controller.abort();
  }, [projectName, episode, kind]);

  const blockedAdmission =
    refusal ?? (preview?.admission?.decision === "blocked" ? preview.admission : null);

  const cost = costEntries(preview?.estimated_cost ?? undefined)
    .map(([currency, amount]) => formatCurrencyAmount(currency, amount))
    .join(" + ");

  const handleConfirm = async () => {
    setSubmitting(true);
    try {
      const submitted = await enqueueStoryboardBatch(projectName, episode, kind);
      if (submitted.admission && submitted.admission.decision !== "admitted") {
        setRefusal(submitted.admission);
        return;
      }
      onClose();
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setSubmitting(false);
    }
  };

  let body;
  if (error) {
    body = <p role="alert">{t("storyboard_batch_load_failed", { message: error })}</p>;
  } else if (!preview) {
    body = <p>{t("storyboard_batch_loading")}</p>;
  } else {
    body = (
      <div className="flex flex-col gap-3" data-testid="storyboard-batch-preview">
        {preview.targets.length === 0 ? (
          <p>{t("storyboard_batch_nothing")}</p>
        ) : (
          <div className="flex flex-col gap-1">
            <p className="font-medium text-subtle-foreground">
              {t("storyboard_batch_targets", { count: preview.targets.length })}
            </p>
            <div className="flex flex-wrap gap-x-3 gap-y-1.5">
              {preview.targets.map((item) => (
                <EpisodeItemRef key={item.unit_id} itemId={item.unit_id} />
              ))}
            </div>
          </div>
        )}
        {preview.skipped.length > 0 && (
          <div>
            <p className="font-medium text-subtle-foreground">
              {t("storyboard_batch_skipped", { count: preview.skipped.length })}
            </p>
            <ul className="mt-1 flex flex-col gap-1">
              {preview.skipped.map((item) => (
                <li key={item.unit_id} className="flex flex-wrap items-center gap-x-1.5">
                  <EpisodeItemRef itemId={item.unit_id} />
                  <span>{t(`storyboard_batch_skip.${item.reason}`)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {blockedAdmission ? (
          <BatchAdmissionSummary admission={blockedAdmission} />
        ) : (
          preview.targets.length > 0 && (
            <>
              <p>{cost ? t("storyboard_batch_cost", { cost }) : t("storyboard_batch_cost_unknown")}</p>
              {preview.unpriced_count > 0 && (
                <p>{t("storyboard_batch_unpriced", { count: preview.unpriced_count })}</p>
              )}
            </>
          )
        )}
        <p>{t(`storyboard_batch_stale_note.${kind}`)}</p>
      </div>
    );
  }

  const title = t(`batch_generate_${kind}`);
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
        {/* Skipped lists may be long; allow keyboard scrolling in the body */}
        <AlertDialogBody tabIndex={0} role="region" aria-label={title}>
          <AlertDialogDescription render={<div />}>{body}</AlertDialogDescription>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={submitting}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction
            disabled={submitting || !preview || preview.targets.length === 0 || blockedAdmission !== null}
            onClick={() => void handleConfirm()}
          >
            {submitting ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
            {submitting ? t("storyboard_batch_submitting") : t("storyboard_batch_confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
