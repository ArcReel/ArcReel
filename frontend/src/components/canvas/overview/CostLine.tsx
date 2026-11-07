import { useTranslation } from "react-i18next";
import { Link } from "wouter";

import { settingsSectionPath } from "@/app-routes";
import { Button, buttonVariants } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { useCostStore } from "@/stores/cost-store";
import type { CostByType, UnpricedModel } from "@/types";
import { costEntries, formatCostTotal, totalBreakdown } from "@/utils/cost-format";
import { customModelSettingsPath } from "@/utils/output-truncation";

type MediaCallType = UnpricedModel["call_type"];

interface CostRow {
  label: string;
  value: string;
}

function CostColumn({ label, rows, total }: { label: string; rows: CostRow[]; total: string }) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="min-w-0">
      <div className="mb-1.5 text-xs font-medium text-muted-foreground">{label}</div>
      <dl className="num flex flex-col gap-1 text-sm">
        {rows.map((row) => (
          <div key={row.label} className="flex gap-2">
            <dt className="text-muted-foreground">{row.label}</dt>
            <dd className="flex-1 text-right text-subtle-foreground">{row.value}</dd>
          </div>
        ))}
        <div className="mt-1 flex gap-2 border-t border-border pt-2">
          <dt className="text-muted-foreground">{t("overview_cost_total")}</dt>
          <dd className="flex-1 text-right font-semibold text-foreground">{total}</dd>
        </div>
      </dl>
    </div>
  );
}

/** Deduplicate unpriced models; retain custom-provider models with navigable pricing settings only. */
function priceTargets(models: UnpricedModel[]): { key: string; label: string; href: string }[] {
  const targets = new Map<string, { key: string; label: string; href: string }>();
  for (const model of models) {
    const key = `${model.provider}/${model.model}`;
    const path = customModelSettingsPath(model.provider, model.model);
    if (path && !targets.has(key)) {
      targets.set(key, { key, label: `${model.provider_name} / ${model.model}`, href: `~${path}` });
    }
  }
  return [...targets.values()];
}

const countOf = (models: UnpricedModel[]) => models.reduce((sum, model) => sum + model.count, 0);

/**
 * Cost row: estimated/spent amounts; details opens estimated/actual columns in a Popover, with a project-usage link below.
 * Data comes from `useCostStore` project totals (`costData.project_totals`), fetched per project on overview mount.
 *
 * Distinguish zero from unknown totals: unpriced portions or no local generation records display “—” with explanations below.
 * For unpriced models, list/link corresponding custom-provider models. Partly unpriced totals still show priced amounts.
 * Unavailable data (unloaded, failed, another project) also displays “—”. Read-only demos are not local and have no cost data.
 */
export function CostLine({ projectName, readOnly = false }: { projectName: string; readOnly?: boolean }) {
  const { t } = useTranslation("dashboard");
  const costData = useCostStore((s) => s.costData);
  const loading = useCostStore((s) => s.loading);
  const error = useCostStore((s) => s.error);
  // Singleton store may retain previous-project data during project switching.
  const data = costData?.project_name === projectName ? costData : undefined;
  const totals = data?.project_totals;
  const unpricedEstimate = data?.unpriced.estimate ?? [];
  const unpricedActual = data?.unpriced.actual ?? [];
  const recorded = !data?.missing_local_calls;
  const estimateComplete = unpricedEstimate.length === 0;
  const actualComplete = unpricedActual.length === 0 && recorded;
  // Evaluate detail rows individually: only categories with unpriced calls show “—”; others still distinguish real zero.
  const estimateUnpriced = new Set(unpricedEstimate.map((model) => model.call_type));
  const actualUnpriced = new Set(unpricedActual.map((model) => model.call_type));

  const typeRows = (by: CostByType, unpriced: Set<MediaCallType>, recorded: boolean): CostRow[] => [
    { label: t("storyboard"), value: formatCostTotal(by.image, recorded && !unpriced.has("image")) },
    { label: t("video"), value: formatCostTotal(by.video, recorded && !unpriced.has("video")) },
    ...(costEntries(by.audio).length > 0 || unpriced.has("audio")
      ? [{ label: t("media_narration_title"), value: formatCostTotal(by.audio, recorded && !unpriced.has("audio")) }]
      : []),
  ];
  const actualRows = (by: CostByType): CostRow[] => [
    ...typeRows(by, actualUnpriced, recorded),
    // All sheets come from image calls.
    ...(["characters", "scenes", "props", "products"] as const)
      .filter((kind) => by[kind] != null)
      .map((kind) => ({
        label: t(`actual_${kind}`),
        value: formatCostTotal(by[kind], recorded && !actualUnpriced.has("image")),
      })),
    ...(costEntries(by.unassigned).length > 0
      ? [{ label: t("actual_unassigned_history"), value: formatCostTotal(by.unassigned, actualComplete) }]
      : []),
  ];

  const estimate = totals ? formatCostTotal(totalBreakdown(totals.estimate), estimateComplete) : "—";
  const spent = totals ? formatCostTotal(totalBreakdown(totals.actual), actualComplete) : "—";

  const notes: string[] = [];
  if (readOnly) notes.push(t("overview_cost_demo"));
  if (data?.missing_local_calls) notes.push(t("overview_cost_no_local_calls"));
  if (unpricedEstimate.length > 0) {
    notes.push(t("overview_cost_unpriced_estimate", { count: countOf(unpricedEstimate) }));
  }
  if (unpricedActual.length > 0) {
    notes.push(t("overview_cost_unpriced_actual", { count: countOf(unpricedActual) }));
  }
  const targets = priceTargets([...unpricedEstimate, ...unpricedActual]);

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="text-muted-foreground">{t("overview_cost_label")}</span>
        <span className="num flex flex-wrap items-center gap-x-1.5 text-subtle-foreground">
          <span>{t("overview_cost_estimate", { amount: estimate })}</span>
          <span aria-hidden className="text-muted-foreground">
            ·
          </span>
          <span>{t("overview_cost_spent", { amount: spent })}</span>
        </span>
        {loading && !totals ? (
          <span role="status" className="text-muted-foreground">
            {t("calculating_cost")}
          </span>
        ) : null}
        {error && !loading ? (
          <span role="alert" className="text-destructive">
            {t("cost_estimate_failed", { message: error })}
          </span>
        ) : null}
        {totals ? (
          <Popover>
            <PopoverTrigger render={<Button variant="link" size="sm" />}>{t("overview_cost_details")}</PopoverTrigger>
            <PopoverContent align="start" className="w-120">
              <PopoverTitle className="sr-only">{t("overview_cost_details_title")}</PopoverTitle>
              <div className="grid grid-cols-2 gap-5 p-1">
                <CostColumn
                  label={t("overview_cost_estimate_column")}
                  rows={typeRows(totals.estimate, estimateUnpriced, true)}
                  total={estimate}
                />
                <CostColumn label={t("overview_cost_actual_column")} rows={actualRows(totals.actual)} total={spent} />
              </div>
              <div className="flex justify-end border-t border-border pt-1">
                <Link
                  href={`~${settingsSectionPath("usage", { u_project: projectName })}`}
                  className={buttonVariants({ variant: "link", size: "sm" })}
                >
                  {t("overview_cost_usage_link")}
                </Link>
              </div>
            </PopoverContent>
          </Popover>
        ) : null}
      </div>
      {notes.length > 0 ? (
        <ul className="flex flex-col gap-0.5 text-xs text-muted-foreground">
          {notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
          {targets.length > 0 ? (
            <li className="flex flex-col items-start">
              <span>{t("overview_cost_set_price")}</span>
              {targets.map((target) => (
                <Link
                  key={target.key}
                  href={target.href}
                  className="focus-ring rounded-sm py-1 break-all text-primary underline underline-offset-4"
                >
                  {target.label}
                </Link>
              ))}
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}
