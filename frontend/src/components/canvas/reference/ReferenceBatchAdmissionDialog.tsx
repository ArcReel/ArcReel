import { useTranslation } from "react-i18next";
import { AlertTriangle } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { BatchAdmissionSummary } from "@/components/workflow/BatchAdmissionSummary";
import { ProblemList } from "@/components/workflow/ProblemList";
import { enqueueFailureViews } from "@/components/workflow/problem-views";
import type { ReferenceBatchAdmission } from "@/types";
import { referenceBatchOutcome, type ReferenceBatchOutcome } from "./batch-outcome";

interface Props {
  /**
   * Do not show whole-batch enqueue success, reported by toast. Explain four other outcomes in place:
   * confirmation needed, blocked, and units not queued after interruption, separately noting when none queued.
   */
  admission: ReferenceBatchAdmission | null;
  /** Resend batch requests using confirmation.tiers. */
  onConfirm: () => void;
  onClose: () => void;
}

/** Titles for four stated outcomes; `queued` never opens dialogs and is excluded. */
const TITLE_KEYS: Record<Exclude<ReferenceBatchOutcome, "queued">, string> = {
  confirm: "reference_batch_confirm_title",
  blocked: "reference_batch_blocked_title",
  interrupted: "reference_batch_enqueue_interrupted_title",
  none_queued: "reference_batch_enqueue_none_queued_title",
};

/**
 * Batch-video generation outcome dialog.
 *
 * {@link BatchAdmissionSummary} supplies admission prose, identical to inline workflow-panel statements,
 * without independently recomputing decisions. Enqueue interruption happens after admission; explain locally because admission passed
 * and gaps are not within units. Workflow statements that no batch tasks were created do not fit this outcome.
 *
 * All four forms share title, focus acquisition, and buttons. Only confirmation requires user decisions; others
 * state already-occurring outcomes, using acknowledgment buttons. How to resolve listed problems belongs to each unit
 * step; dialogs do not infer next actions for users.
 */
export function ReferenceBatchAdmissionDialog({ admission, onConfirm, onClose }: Props) {
  const { t } = useTranslation("dashboard");
  const { t: tWorkflow } = useTranslation("workflow");
  const { t: tCommon } = useTranslation("common");

  const outcome = admission && referenceBatchOutcome(admission);
  // Both incomplete-enqueue outcomes list all unqueued units; only titles/openings differ:
  // tasks still running versus no tasks created are distinct outcomes for users.
  const enqueueGap = outcome === "interrupted" || outcome === "none_queued";
  const open = outcome !== null && outcome !== "queued";
  // Blocked/interrupted enqueue are existing bad news with warning icons; confirmation has no failure yet, so no icon.
  const warned = enqueueGap || outcome === "blocked";
  const title = outcome && outcome !== "queued" ? t(TITLE_KEYS[outcome]) : "";

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <AlertDialogContent size="lg">
        <AlertDialogHeader>
          {warned && (
            <AlertDialogMedia>
              <AlertTriangle aria-hidden className="text-warn" />
            </AlertDialogMedia>
          )}
          <AlertDialogTitle>{title}</AlertDialogTitle>
        </AlertDialogHeader>
        {/* Many unqueued units may require keyboard-scrollable body content. */}
        <AlertDialogBody tabIndex={0} role="region" aria-label={title}>
          <AlertDialogDescription render={<div />}>
            {admission &&
              (enqueueGap ? (
                <div className="space-y-2">
                  <p>
                    {t(
                      outcome === "interrupted"
                        ? "reference_batch_enqueue_interrupted_intro"
                        : "reference_batch_enqueue_none_queued_intro",
                      { count: admission.enqueue_failures.length },
                    )}
                  </p>
                  <ProblemList problems={enqueueFailureViews(tWorkflow, admission.enqueue_failures)} />
                  {/* Other forms use BatchAdmissionSummary to state skipped units; this form bypasses it,
                      so add the same statement here for complete batch-outcome reporting. */}
                  {admission.skipped_unit_ids.length > 0 && (
                    <p>{tWorkflow("admission_skipped", { count: admission.skipped_unit_ids.length })}</p>
                  )}
                </div>
              ) : (
                <BatchAdmissionSummary admission={admission} skippedUnitIds={admission.skipped_unit_ids} />
              ))}
          </AlertDialogDescription>
        </AlertDialogBody>
        <AlertDialogFooter>
          {warned ? (
            <AlertDialogAction onClick={onClose}>{t("reference_batch_ack_cta")}</AlertDialogAction>
          ) : (
            <>
              <AlertDialogCancel>{tCommon("cancel")}</AlertDialogCancel>
              <AlertDialogAction onClick={onConfirm}>{t("reference_batch_confirm_cta")}</AlertDialogAction>
            </>
          )}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
