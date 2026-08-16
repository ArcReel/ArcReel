import { useId } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle } from "lucide-react";
import { GlassModal } from "@/components/ui/GlassModal";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { BatchAdmissionSummary } from "@/components/workflow/BatchAdmissionSummary";
import { WARM_TONE } from "@/utils/severity-tone";
import type { ReferenceBatchAdmission } from "@/types";

interface Props {
  /** null 或 decision=admitted 时不展示——已建任务的结局由 toast 反馈。 */
  admission: ReferenceBatchAdmission | null;
  /** 按 confirmation.tiers 的档位重发批量请求 */
  onConfirm: () => void;
  onClose: () => void;
}

/**
 * 批量视频生成的准入结论弹窗。
 *
 * 结论正文由 {@link BatchAdmissionSummary} 给出——工作流面板就地摊开的是同一份陈述，
 * 两处不各推一遍判定。本组件只负责当场拍板需要的外壳：标题、抢焦与两个按钮。
 */
export function ReferenceBatchAdmissionDialog({ admission, onConfirm, onClose }: Props) {
  const { t } = useTranslation("dashboard");
  const { t: tCommon } = useTranslation("common");
  const titleId = useId();
  const descId = useId();

  const open = admission !== null && admission.decision !== "admitted";
  const blocked = admission?.decision === "blocked";

  return (
    <GlassModal
      open={open}
      onClose={onClose}
      labelledBy={titleId}
      describedBy={descId}
      hairlineTone={blocked ? "warm" : "accent"}
      widthClassName="w-full max-w-lg"
    >
      <div className="px-6 pb-6 pt-5">
        <div className="flex items-start gap-3">
          {blocked && (
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
              {blocked ? t("reference_batch_blocked_title") : t("reference_batch_confirm_title")}
            </h2>
            <div id={descId} className="mt-1">
              {admission && (
                <BatchAdmissionSummary
                  admission={admission}
                  skippedUnitIds={admission.skipped_unit_ids}
                />
              )}
            </div>
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          {blocked ? (
            <PrimaryButton size="sm" tone="warm" onClick={onClose}>
              {t("reference_batch_blocked_cta")}
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
