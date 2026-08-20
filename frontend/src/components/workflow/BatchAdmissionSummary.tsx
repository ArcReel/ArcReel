import { useTranslation } from "react-i18next";
import { formatCurrencyAmount } from "@/utils/cost-format";
import type { WorkflowAdmission } from "@/types/workflow";
import { ProblemList } from "./ProblemList";
import { UnitTag } from "./UnitTag";
import { admissionUnitViews, isWithheld } from "./problem-views";

interface Props {
  admission: WorkflowAdmission;
  /** 已有产物、本次跳过的单元数；批量端点才有这份信息。 */
  skippedUnitIds?: string[];
  className?: string;
}

/**
 * 整批准入判定的结论正文。两种未提交的结局共用它，因为它们说的是同一件事——
 * 「这一批一个任务也没有创建，原因如下」：
 *
 * - `confirmation_required`：按申请档位分组陈述秒数 × 单元数与合计费用，用户按整批拍板。
 *   分组而非逐个列行——同档位的单元讲的是同一件事，逐行重复会淹没档位本身；每档仍列出
 *   全部 unit_id，用户才知道自己在为谁拍板。
 * - `blocked`：逐个列出全部缺口，不塌成一句通用错误。真正有问题的排在前面，被它们连带
 *   扣下的列在后面并标明原因，用户一眼分得清该去修哪几个。
 *
 * 正文与外壳分开：参考生视频把它装进弹窗当场拍板，工作流面板把它就地摊在视频步骤下，
 * 两处陈述同一份结论、共用同一段判定，不各推一遍。
 */
export function BatchAdmissionSummary({ admission, skippedUnitIds, className }: Props) {
  const { t } = useTranslation("workflow");
  const blocked = admission.decision === "blocked";
  const tiers = admission.confirmation?.tiers ?? [];
  const seconds = (value: number) => t("duration_seconds", { value });
  const tierSeconds = (value: number | null) =>
    value == null ? t("tier_unknown") : seconds(value);

  const failing = admission.units.filter((unit) => !unit.admitted && !isWithheld(unit));
  const withheld = admission.units.filter(isWithheld);
  const confirmingUnitCount = tiers.reduce((sum, tier) => sum + tier.unit_count, 0);
  const skippedCount = skippedUnitIds?.length ?? 0;

  return (
    <div className={className ?? "space-y-2 text-[12.5px] leading-relaxed"}>
      {blocked ? (
        <>
          <p style={{ color: "var(--color-text-3)" }}>{t("admission_blocked_intro")}</p>
          <ProblemList
            problems={admissionUnitViews(t, failing, seconds)}
            className="max-h-56 space-y-2 overflow-y-auto"
          />
          {withheld.length > 0 && (
            <div className="space-y-1">
              <p style={{ color: "var(--color-text-4)" }}>
                {t("admission_withheld_title", { count: withheld.length })}
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
          <p style={{ color: "var(--color-text-3)" }}>
            {t("admission_confirm_intro", { count: confirmingUnitCount })}
          </p>
          <ul className="max-h-56 space-y-2 overflow-y-auto">
            {tiers.map((tier) => (
              <li key={tier.request_duration_seconds ?? "unknown"} className="space-y-1">
                <span className="flex flex-wrap items-baseline gap-x-2">
                  <span className="tabular-nums font-medium" style={{ color: "var(--color-text)" }}>
                    {tierSeconds(tier.request_duration_seconds)}
                  </span>
                  <span aria-hidden style={{ color: "var(--color-text-4)" }}>
                    ×
                  </span>
                  <span className="tabular-nums" style={{ color: "var(--color-text-2)" }}>
                    {t("tier_units", { count: tier.unit_count })}
                  </span>
                  <span style={{ color: "var(--color-text-2)" }}>
                    {tier.cost_amount != null && tier.cost_currency
                      ? t("tier_cost", {
                          cost: formatCurrencyAmount(tier.cost_currency, tier.cost_amount),
                        })
                      : t("tier_cost_unknown")}
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
          <p style={{ color: "var(--color-text-3)" }}>{t("admission_confirm_note")}</p>
        </>
      )}
      {skippedCount > 0 && (
        <p style={{ color: "var(--color-text-3)" }}>
          {t("admission_skipped", { count: skippedCount })}
        </p>
      )}
    </div>
  );
}
