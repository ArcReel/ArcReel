import { useId } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle } from "lucide-react";
import { GlassModal } from "@/components/ui/GlassModal";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { WARM_TONE } from "@/utils/severity-tone";
import { formatCurrencyAmount } from "@/utils/cost-format";
import type { ReferenceBatchAdmission, ReferenceBatchUnitOutcome } from "@/types";

/** 受阻批次里「自身没问题、随本批一起未提交」的标记。 */
const WITHHELD_CODE = "generation_batch_admission_withheld";

interface Props {
  /** null 或 decision=admitted 时不展示——已建任务的结局由 toast 反馈。 */
  admission: ReferenceBatchAdmission | null;
  /** 按 confirmation.tiers 的档位重发批量请求 */
  onConfirm: () => void;
  onClose: () => void;
}

/** unit_id 的等宽标签：与画布单元列表同一书写形态，便于用户在两处对上号。 */
function UnitTag({ unitId }: { unitId: string }) {
  return (
    <span
      translate="no"
      className="rounded-md px-1.5 py-0.5 font-mono text-[11.5px]"
      style={{ background: "var(--color-surface-2)", color: "var(--color-text-2)" }}
    >
      {unitId}
    </span>
  );
}

/** 一行缺口：单元 + 已本地化的说明 + 下一步。 */
function ProblemRow({ unit }: { unit: ReferenceBatchUnitOutcome }) {
  const { t } = useTranslation("dashboard");
  return (
    <li className="flex flex-col gap-0.5">
      <span className="flex flex-wrap items-baseline gap-x-2">
        <UnitTag unitId={unit.unit_id} />
        <span style={{ color: "var(--color-text-2)" }}>
          {unit.problems.map((problem) => problem.message ?? problem.detail ?? problem.code).join(" · ")}
        </span>
      </span>
      {unit.problems.map((problem, index) => {
        const hint = problem.action
          ? t(`reference_batch_action_${problem.action}`, { defaultValue: "" })
          : "";
        if (!hint) return null;
        return (
          <span
            key={`${problem.code}-${index}`}
            className="text-[11.5px]"
            style={{ color: "var(--color-text-3)" }}
          >
            {hint}
          </span>
        );
      })}
    </li>
  );
}

/**
 * 批量视频生成的准入结论展示。两种结局共用一个面板，因为它们说的是同一件事——
 * 「这批还没开始生成，原因如下」：
 *
 * - `confirmation_required`：按申请档位分组陈述秒数 × 单元数与合计费用，用户按整批拍板。
 *   分组而非逐个列行——批量里同档位的单元讲的是同一件事，逐行重复会淹没档位本身；
 *   每档仍列出全部 unit_id，用户才知道自己在为谁拍板。
 * - `blocked`：一个任务也没建，故逐个列出全部缺口而不是塌成一句通用错误。真正有问题的
 *   排在前面，被它们连带扣下的单元列在后面并标明原因，用户一眼能分清该去修哪几个。
 */
export function ReferenceBatchAdmissionDialog({ admission, onConfirm, onClose }: Props) {
  const { t } = useTranslation("dashboard");
  const { t: tCommon } = useTranslation("common");
  const titleId = useId();
  const descId = useId();

  const open = admission !== null && admission.decision !== "admitted";
  const blocked = admission?.decision === "blocked";
  const tiers = admission?.confirmation?.tiers ?? [];
  const seconds = (value: number) => t("reference_duration_seconds", { value });

  const failing = (admission?.units ?? []).filter(
    (unit) => !unit.admitted && unit.problems.some((problem) => problem.code !== WITHHELD_CODE),
  );
  const withheld = (admission?.units ?? []).filter(
    (unit) =>
      !unit.admitted && unit.problems.length > 0 && unit.problems.every((p) => p.code === WITHHELD_CODE),
  );
  const confirmingUnitCount = tiers.reduce((sum, tier) => sum + tier.unit_count, 0);
  const skipped = admission?.skipped_unit_ids ?? [];

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
            <div
              id={descId}
              className="mt-1 space-y-2 text-[12.5px] leading-relaxed"
              style={{ color: "var(--color-text-3)" }}
            >
              {blocked ? (
                <>
                  <p>{t("reference_batch_blocked_intro")}</p>
                  <ul className="max-h-56 space-y-2 overflow-y-auto">
                    {failing.map((unit) => (
                      <ProblemRow key={unit.unit_id} unit={unit} />
                    ))}
                  </ul>
                  {withheld.length > 0 && (
                    <div className="space-y-1">
                      <p style={{ color: "var(--color-text-4)" }}>
                        {t("reference_batch_withheld_title", { count: withheld.length })}
                      </p>
                      <div className="flex flex-wrap gap-1">
                        {withheld.map((unit) => (
                          <UnitTag key={unit.unit_id} unitId={unit.unit_id} />
                        ))}
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <>
                  <p>{t("reference_batch_confirm_intro", { count: confirmingUnitCount })}</p>
                  <ul className="max-h-56 space-y-2 overflow-y-auto">
                    {tiers.map((tier) => (
                      <li key={tier.request_duration_seconds} className="space-y-1">
                        <span className="flex flex-wrap items-baseline gap-x-2">
                          <span
                            className="tabular-nums font-medium"
                            style={{ color: "var(--color-text)" }}
                          >
                            {seconds(tier.request_duration_seconds)}
                          </span>
                          <span aria-hidden style={{ color: "var(--color-text-4)" }}>
                            ×
                          </span>
                          <span className="tabular-nums" style={{ color: "var(--color-text-2)" }}>
                            {t("reference_batch_tier_units", { count: tier.unit_count })}
                          </span>
                          <span style={{ color: "var(--color-text-2)" }}>
                            {tier.cost_amount != null && tier.cost_currency
                              ? t("reference_batch_tier_cost", {
                                  cost: formatCurrencyAmount(tier.cost_currency, tier.cost_amount),
                                })
                              : t("reference_batch_tier_cost_unknown")}
                          </span>
                        </span>
                        <span className="flex flex-wrap gap-1">
                          {tier.unit_ids.map((unitId) => (
                            <UnitTag key={unitId} unitId={unitId} />
                          ))}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <p>{t("reference_duration_note_no_trim")}</p>
                </>
              )}
              {skipped.length > 0 && <p>{t("reference_batch_skipped", { count: skipped.length })}</p>}
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
