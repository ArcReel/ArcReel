import { useTranslation } from "react-i18next";
import { Link } from "wouter";

import { settingsSectionPath } from "@/app-routes";
import { Button, buttonVariants } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { useCostStore } from "@/stores/cost-store";
import type { CostByType } from "@/types";
import { costEntries, formatCost, totalBreakdown } from "@/utils/cost-format";

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

/**
 * 费用一行：「费用 预估 … · 已花 …」，「明细」打开预估与实际两列的 Popover，底部链接到本项目的使用记录。
 * 数据来自 `useCostStore` 的项目合计（`costData.project_totals`），由概览挂载时按项目拉取；
 * 拿不到数字（未加载、加载失败、属于别的项目、没有任何记录）时显示「—」。
 */
export function CostLine({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const costData = useCostStore((s) => s.costData);
  const loading = useCostStore((s) => s.loading);
  const error = useCostStore((s) => s.error);
  // store 是单例，切项目的窗口期里可能还留着上一个项目的数据
  const totals = costData?.project_name === projectName ? costData.project_totals : undefined;

  const typeRows = (by: CostByType): CostRow[] => [
    { label: t("storyboard"), value: formatCost(by.image) },
    { label: t("video"), value: formatCost(by.video) },
    ...(costEntries(by.audio).length > 0 ? [{ label: t("media_narration_title"), value: formatCost(by.audio) }] : []),
  ];
  const actualRows = (by: CostByType): CostRow[] => [
    ...typeRows(by),
    ...(["characters", "scenes", "props", "products"] as const)
      .filter((kind) => by[kind] != null)
      .map((kind) => ({ label: t(`actual_${kind}`), value: formatCost(by[kind]) })),
    ...(costEntries(by.unassigned).length > 0
      ? [{ label: t("actual_unassigned_history"), value: formatCost(by.unassigned) }]
      : []),
  ];

  const estimate = totals ? formatCost(totalBreakdown(totals.estimate)) : "—";
  const spent = totals ? formatCost(totalBreakdown(totals.actual)) : "—";

  return (
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
          <PopoverTrigger render={<Button variant="link" size="sm" />}>
            {t("overview_cost_details")}
          </PopoverTrigger>
          <PopoverContent align="start" className="w-120">
            <PopoverTitle className="sr-only">{t("overview_cost_details_title")}</PopoverTitle>
            <div className="grid grid-cols-2 gap-5 p-1">
              <CostColumn
                label={t("overview_cost_estimate_column")}
                rows={typeRows(totals.estimate)}
                total={formatCost(totalBreakdown(totals.estimate))}
              />
              <CostColumn
                label={t("overview_cost_actual_column")}
                rows={actualRows(totals.actual)}
                total={formatCost(totalBreakdown(totals.actual))}
              />
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
  );
}
