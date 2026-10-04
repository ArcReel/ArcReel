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
   * 整批入队成功时不展示——那一路的结局由 toast 反馈。其余四种结局都要当场说清楚：
   * 需确认、受阻，以及入队中断后哪几个单元没排上（一个都没排上时另说一句）。
   */
  admission: ReferenceBatchAdmission | null;
  /** 按 confirmation.tiers 的档位重发批量请求 */
  onConfirm: () => void;
  onClose: () => void;
}

/** 要陈述的四种结局各自的标题。`queued` 不开弹窗，不在表内。 */
const TITLE_KEYS: Record<Exclude<ReferenceBatchOutcome, "queued">, string> = {
  confirm: "reference_batch_confirm_title",
  blocked: "reference_batch_blocked_title",
  interrupted: "reference_batch_enqueue_interrupted_title",
  none_queued: "reference_batch_enqueue_none_queued_title",
};

/**
 * 批量视频生成的结论弹窗。
 *
 * 准入结论的正文由 {@link BatchAdmissionSummary} 给出——工作流面板就地摊开的是同一份
 * 陈述，两处不各推一遍判定。入队中断是准入之后的结局，正文在本组件就地给出：准入已经
 * 通过，缺口不在单元自身，工作流面板那份「这一批一个任务也没建」的陈述套不上它。
 *
 * 四种形态共用一套外壳：标题、抢焦与按钮。只有需确认那一种要用户拍板，其余都只是
 * 陈述已经发生的事，收尾按钮统一是「知道了」——列出的问题该怎么办属于各单元自己的
 * 步骤，弹窗不替用户推断下一步。
 */
export function ReferenceBatchAdmissionDialog({ admission, onConfirm, onClose }: Props) {
  const { t } = useTranslation("dashboard");
  const { t: tWorkflow } = useTranslation("workflow");
  const { t: tCommon } = useTranslation("common");

  const outcome = admission && referenceBatchOutcome(admission);
  // 入队留下缺口的两路正文相同——都是逐个列出没排上的单元，只有标题与开场白分开：
  // 还有任务在跑，与一个任务也没建成，对用户不是同一件事。
  const enqueueGap = outcome === "interrupted" || outcome === "none_queued";
  const open = outcome !== null && outcome !== "queued";
  // 受阻与入队中断都是已经发生的坏消息，标题前加警告图标；需确认那一种还没出事，不加。
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
        {/* 未排上队列的单元可能很多，正文可以用键盘滚动 */}
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
                  {/* 另两种形态由 BatchAdmissionSummary 交代已跳过的单元，这一路的正文
                      不走它，同一句话在这里补上，免得「这一批发生了什么」缺一角。 */}
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
