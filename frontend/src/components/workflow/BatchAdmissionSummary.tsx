import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { formatCurrencyAmount } from "@/utils/cost-format";
import type { WorkflowAdmission } from "@/types/workflow";
import { ProblemList } from "./ProblemList";
import { EpisodeItemRef } from "@/components/shared/episode-item/EpisodeItemRef";
import { admissionUnitViews, isWithheld } from "./problem-views";

interface Props {
  admission: WorkflowAdmission;
  /** Units skipped because artifacts exist, available from batch endpoints only. */
  skippedUnitIds?: string[];
  className?: string;
}

/**
 * Whole-batch admission conclusions. Both unsubmitted outcomes share contents because both explain:
 * no tasks were created for these reasons:
 *
 * - `confirmation_required`: group seconds × unit counts and total costs by requested tiers for whole-batch decisions.
 *   Groups avoid duplicate unit rows obscuring tiers, while still listing
 *   every unit_id so users know what they approve.
 * - `blocked`: list every gap individually, never generic errors. Actual problems precede
 *   consequentially blocked units with causes, making repair targets clear.
 *
 * Contents are shell-independent: reference video uses decision dialogs, workflow panels show them beneath video steps;
 * both share identical conclusions/predicates without recomputation. Contents never scroll independently; shells (dialog Body,
 * production-progress overlays) are sole scroll areas.
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
    <div className={cn("flex flex-col gap-2 text-sm leading-relaxed", className)}>
      {blocked ? (
        <>
          <p className="text-subtle-foreground">{t("admission_blocked_intro")}</p>
          <ProblemList problems={admissionUnitViews(t, failing, seconds)} className="gap-2" />
          {withheld.length > 0 && (
            <div className="flex flex-col gap-1">
              <p className="text-subtle-foreground">{t("admission_withheld_title", { count: withheld.length })}</p>
              <div className="flex flex-wrap gap-1">
                {withheld.map((unit) => (
                  <EpisodeItemRef key={unit.unit_id} itemId={unit.unit_id} />
                ))}
              </div>
            </div>
          )}
        </>
      ) : (
        <>
          <p className="text-subtle-foreground">{t("admission_confirm_intro", { count: confirmingUnitCount })}</p>
          <ul className="flex flex-col gap-2">
            {tiers.map((tier) => (
              <li key={tier.request_duration_seconds ?? "unknown"} className="flex flex-col gap-1">
                <span className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium text-foreground tabular-nums">
                    {tierSeconds(tier.request_duration_seconds)}
                  </span>
                  <span aria-hidden className="text-subtle-foreground">
                    ×
                  </span>
                  <span className="text-subtle-foreground tabular-nums">
                    {t("tier_units", { count: tier.unit_count })}
                  </span>
                  <span className="text-subtle-foreground">
                    {tier.cost_amount != null && tier.cost_currency
                      ? t("tier_cost", { cost: formatCurrencyAmount(tier.cost_currency, tier.cost_amount) })
                      : t("tier_cost_unknown")}
                  </span>
                  {(tier.unpriced_count ?? 0) > 0 && (
                    <span className="text-subtle-foreground tabular-nums">
                      {t("tier_unpriced", { count: tier.unpriced_count })}
                    </span>
                  )}
                </span>
                <span className="flex flex-wrap gap-1">
                  {tier.unit_ids.map((unitId) => (
                    <EpisodeItemRef key={unitId} itemId={unitId} />
                  ))}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-subtle-foreground">{t("admission_confirm_note")}</p>
        </>
      )}
      {skippedCount > 0 && <p className="text-subtle-foreground">{t("admission_skipped", { count: skippedCount })}</p>}
    </div>
  );
}
