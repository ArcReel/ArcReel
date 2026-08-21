import { useId } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle } from "lucide-react";
import { GlassModal } from "@/components/ui/GlassModal";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { BatchAdmissionSummary } from "@/components/workflow/BatchAdmissionSummary";
import { ProblemList } from "@/components/workflow/ProblemList";
import { enqueueFailureViews } from "@/components/workflow/problem-views";
import { WARM_TONE } from "@/utils/severity-tone";
import type { ReferenceBatchAdmission } from "@/types";
import { referenceBatchOutcome } from "./batch-outcome";

interface Props {
  /**
   * 整批入队成功时不展示——那一路的结局由 toast 反馈。其余三种结局都要当场说清楚：
   * 需确认、受阻，以及入队中断后哪几个单元没排上。
   */
  admission: ReferenceBatchAdmission | null;
  /** 按 confirmation.tiers 的档位重发批量请求 */
  onConfirm: () => void;
  onClose: () => void;
}

/**
 * 批量视频生成的结论弹窗。
 *
 * 准入结论的正文由 {@link BatchAdmissionSummary} 给出——工作流面板就地摊开的是同一份
 * 陈述，两处不各推一遍判定。入队中断是准入之后的结局，正文在本组件就地给出：准入已经
 * 通过，缺口不在单元自身，工作流面板那份「这一批一个任务也没建」的陈述套不上它。
 *
 * 三种形态共用一套外壳：标题、抢焦与按钮。只有需确认那一种要用户拍板，另两种都只是
 * 陈述已经发生的事，收尾按钮统一是「知道了」——列出的问题该怎么办属于各单元自己的
 * 步骤，弹窗不替用户推断下一步。
 */
export function ReferenceBatchAdmissionDialog({ admission, onConfirm, onClose }: Props) {
  const { t } = useTranslation("dashboard");
  const { t: tWorkflow } = useTranslation("workflow");
  const { t: tCommon } = useTranslation("common");
  const titleId = useId();
  const descId = useId();

  const outcome = admission && referenceBatchOutcome(admission);
  const interrupted = outcome === "interrupted";
  const open = outcome !== null && outcome !== "queued";
  // 受阻与入队中断都是已经发生的坏消息，共用暖色外壳；需确认那一种还没出事，用强调色。
  const warned = interrupted || outcome === "blocked";

  return (
    <GlassModal
      open={open}
      onClose={onClose}
      labelledBy={titleId}
      describedBy={descId}
      hairlineTone={warned ? "warm" : "accent"}
      widthClassName="w-full max-w-lg"
    >
      <div className="px-6 pb-6 pt-5">
        <div className="flex items-start gap-3">
          {warned && (
            <span
              aria-hidden
              className="grid h-9 w-9 shrink-0 place-items-center rounded-xl"
              style={{
                background:
                  "linear-gradient(135deg, var(--color-warm-tint), var(--color-warm-tint-faint))",
                border: `1px solid ${WARM_TONE.ring}`,
                color: WARM_TONE.color,
                boxShadow: `0 8px 18px -8px ${WARM_TONE.glow}`,
              }}
            >
              <AlertTriangle className="h-4 w-4" />
            </span>
          )}
          <div className="min-w-0 flex-1">
            <h2
              id={titleId}
              className="display-serif text-[17px] font-semibold tracking-tight"
              style={{ color: "var(--color-text)" }}
            >
              {interrupted
                ? t("reference_batch_enqueue_interrupted_title")
                : outcome === "blocked"
                  ? t("reference_batch_blocked_title")
                  : t("reference_batch_confirm_title")}
            </h2>
            <div id={descId} className="mt-1">
              {admission &&
                (interrupted ? (
                  <div className="space-y-2 text-[12.5px] leading-relaxed">
                    <p style={{ color: "var(--color-text-3)" }}>
                      {t("reference_batch_enqueue_interrupted_intro", {
                        count: admission.enqueue_failures.length,
                      })}
                    </p>
                    <ProblemList
                      problems={enqueueFailureViews(tWorkflow, admission.enqueue_failures)}
                      labelledBy={titleId}
                      className="max-h-56 space-y-2 overflow-y-auto"
                    />
                    {/* 另两种形态由 BatchAdmissionSummary 交代已跳过的单元，这一路的正文
                        不走它，同一句话在这里补上，免得「这一批发生了什么」缺一角。 */}
                    {admission.skipped_unit_ids.length > 0 && (
                      <p style={{ color: "var(--color-text-3)" }}>
                        {tWorkflow("admission_skipped", {
                          count: admission.skipped_unit_ids.length,
                        })}
                      </p>
                    )}
                  </div>
                ) : (
                  <BatchAdmissionSummary
                    admission={admission}
                    skippedUnitIds={admission.skipped_unit_ids}
                  />
                ))}
            </div>
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          {warned ? (
            <PrimaryButton size="sm" tone="warm" onClick={onClose}>
              {t("reference_batch_ack_cta")}
            </PrimaryButton>
          ) : (
            <>
              <SecondaryButton size="sm" onClick={onClose}>
                {tCommon("cancel")}
              </SecondaryButton>
              <PrimaryButton size="sm" onClick={onConfirm}>
                {t("reference_batch_confirm_cta")}
              </PrimaryButton>
            </>
          )}
        </div>
      </div>
    </GlassModal>
  );
}
