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
import { UnitTag } from "@/components/workflow/UnitTag";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { costEntries, formatCurrencyAmount } from "@/utils/cost-format";
import type { StoryboardBatchKind, StoryboardBatchPreview, WorkflowAdmission } from "@/types";

/**
 * 一集分镜图 / 分镜视频批量补齐的确认框：列出要生成的分镜、跳过项与原因，能算出时给出预估费用。
 *
 * 分镜视频整批准入：预览时已知不满足就陈述原因、不给提交；预览之后状态有变、提交时才被拒绝的，
 * 同样在框内陈述，一个任务也没建。提交中忽略关闭请求。
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
            <div className="flex flex-wrap gap-1">
              {preview.targets.map((item) => (
                <UnitTag key={item.unit_id} unitId={item.unit_id} />
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
                  <UnitTag unitId={item.unit_id} />
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
            <p>{cost ? t("storyboard_batch_cost", { cost }) : t("storyboard_batch_cost_unknown")}</p>
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
        {/* 跳过清单可能很长，正文可以用键盘滚动 */}
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
