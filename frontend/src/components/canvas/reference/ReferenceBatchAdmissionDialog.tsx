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
  admission: ReferenceBatchAdmission | null;
  onConfirm: () => void;
  onClose: () => void;
}

const TITLE_KEYS: Record<Exclude<ReferenceBatchOutcome, "queued">, string> = {
  confirm: "reference_batch_confirm_title",
  blocked: "reference_batch_blocked_title",
  interrupted: "reference_batch_enqueue_interrupted_title",
  none_queued: "reference_batch_enqueue_none_queued_title",
};

export function ReferenceBatchAdmissionDialog({ admission, onConfirm, onClose }: Props) {
  const { t } = useTranslation("dashboard");
  const { t: tWorkflow } = useTranslation("workflow");
  const { t: tCommon } = useTranslation("common");

  const outcome = admission && referenceBatchOutcome(admission);
  const enqueueGap = outcome === "interrupted" || outcome === "none_queued";
  const open = outcome !== null && outcome !== "queued";
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
