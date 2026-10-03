import { useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import type { UsageStatsBlock, UsageSummary } from "@/types";
import type { UsageRecordsFilters } from "@/stores/usage-records-store";
import { costEntries, formatCurrencyAmount } from "@/utils/cost-format";
import { formatRatio, projectTitleResolver, providerLabelResolver, usageProjectLabel } from "./usage-record-format";

/** 构成表的三个维度；模型行按 (provider, model) 分组。 */
type BreakdownDim = "project" | "provider" | "model";

const DIMS: { value: BreakdownDim; labelKey: string }[] = [
  { value: "project", labelKey: "usage_filter_project" },
  { value: "provider", labelKey: "usage_filter_provider" },
  { value: "model", labelKey: "usage_filter_model" },
];

const NAME_COL_KEYS: Record<BreakdownDim, string> = {
  project: "usage_col_project",
  provider: "usage_col_provider",
  model: "usage_col_model",
};

/** 成功率低于这条线的行用警示色，扫一眼就能挑出问题行。 */
const LOW_SUCCESS_RATE = 0.85;

/** 名称列吃掉剩余宽度，三列数字固定宽度。 */
const GRID_CLS = "grid grid-cols-[minmax(0,1fr)_3.5rem_4rem_6rem] items-center gap-x-2";

/** 一行的展示数据；三个维度投影到同一形状后共用渲染。 */
interface BreakdownRowView {
  key: string;
  name: string;
  /** 模型行的副标题是它的供应商。 */
  sub: string | null;
  stats: UsageStatsBlock;
  /** `other` 行不可点击，它没有对应的筛选值。 */
  filters: Partial<UsageRecordsFilters> | null;
  active: boolean;
}

interface UsageBreakdownCardProps {
  summary: UsageSummary | null;
  filters: UsageRecordsFilters;
  onChange: (patch: Partial<UsageRecordsFilters>) => void;
}

export function UsageBreakdownCard({
  summary,
  filters,
  onChange,
}: UsageBreakdownCardProps) {
  const { t, i18n } = useTranslation("dashboard");
  const [dim, setDim] = useState<BreakdownDim>("provider");
  const providerLabel = providerLabelResolver(summary);
  const titleOf = projectTitleResolver(summary);
  const primary = summary?.primary_currency ?? null;

  const rows = buildRows(dim, summary, filters, {
    providerLabel,
    projectLabel: (name: string) => usageProjectLabel(name, t, i18n.language, titleOf(name)),
    other: (count: number) => t("usage_breakdown_other", { count }),
  });
  const totalCalls = Math.max(1, summary?.kpi.calls ?? 0);

  return (
    <section aria-label={t("usage_breakdown_title")} className="flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{t("usage_breakdown_title")}</h3>
        <div role="group" aria-label={t("usage_breakdown_dim_label")} className="flex items-center gap-1">
          {DIMS.map((option) => {
            const active = dim === option.value;
            return (
              <Button
                key={option.value}
                size="xs"
                variant={active ? "secondary" : "ghost"}
                aria-pressed={active}
                onClick={() => setDim(option.value)}
              >
                {t(option.labelKey)}
              </Button>
            );
          })}
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">{t("usage_breakdown_empty")}</p>
      ) : (
        <div className="flex flex-col">
          <div className={cn(GRID_CLS, "border-b border-border px-2 pb-1.5 text-xs font-medium whitespace-nowrap text-muted-foreground")}>
            <span>{t(NAME_COL_KEYS[dim])}</span>
            <span className="text-right">{t("usage_breakdown_col_calls")}</span>
            <span className="text-right">{t("usage_kpi_success_rate")}</span>
            <span className="text-right">{t("usage_col_cost")}</span>
          </div>
          {rows.map((row) => (
            <BreakdownRow
              key={row.key}
              row={row}
              share={row.stats.calls / totalCalls}
              primary={primary}
              onChange={onChange}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function BreakdownRow({
  row,
  share,
  primary,
  onChange,
}: {
  row: BreakdownRowView;
  share: number;
  primary: string | null;
  onChange: (patch: Partial<UsageRecordsFilters>) => void;
}) {
  const { i18n } = useTranslation("dashboard");
  const content = (
    <>
      {/* 占比条：宽度是该行调用数占总调用数的比例，经 CSS 变量传入 */}
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-1 left-0 w-(--share) rounded-r-xs bg-primary/10"
        style={{ "--share": `${share * 100}%` } as CSSProperties}
      />
      <span className="relative flex min-w-0 items-baseline gap-1.5 text-left">
        <TruncatedText text={row.name} focusable={row.filters === null} />
        {row.sub && <span className="shrink-0 text-xs text-muted-foreground">{row.sub}</span>}
      </span>
      <span className="num relative text-right">{row.stats.calls}</span>
      <span
        className={cn(
          "num relative text-right",
          row.stats.success_rate !== null && row.stats.success_rate < LOW_SUCCESS_RATE && "text-destructive",
        )}
      >
        {formatRatio(row.stats.success_rate, i18n.language)}
      </span>
      <CostCell cost={row.stats.cost} primary={primary} />
    </>
  );

  const shared = cn(GRID_CLS, "relative w-full border-b border-border px-2 py-1.5 text-sm last:border-b-0");

  const patch = row.filters;
  if (patch === null) {
    return <div className={cn(shared, "text-muted-foreground")}>{content}</div>;
  }

  return (
    <button
      type="button"
      aria-pressed={row.active}
      onClick={() => onChange(patch)}
      className={cn(
        shared,
        "rounded-md transition-colors outline-none hover:bg-muted/40 focus-visible:ring-3 focus-visible:ring-ring/50",
        row.active ? "text-foreground" : "text-subtle-foreground",
      )}
    >
      {content}
    </button>
  );
}

function CostCell({ cost, primary }: { cost: Record<string, number>; primary: string | null }) {
  const { t } = useTranslation("dashboard");
  const entries = costEntries(cost);
  const others = entries.filter(([currency]) => currency !== primary);
  const main = primary ? formatCurrencyAmount(primary, cost[primary] ?? 0) : "—";
  const othersText = others
    .map(([currency, amount]) => formatCurrencyAmount(currency, amount))
    .join(" + ");
  return (
    <span className="num relative text-right">
      {main}
      {others.length > 0 && (
        <>
          <span aria-hidden="true" className="ml-1 text-xs text-muted-foreground">
            +{others.length}
          </span>
          <span className="sr-only">
            {` ${t("usage_breakdown_cost_others", { amounts: othersText })}`}
          </span>
        </>
      )}
    </span>
  );
}

interface RowLabels {
  providerLabel: (provider: string | null) => string;
  projectLabel: (name: string) => string;
  other: (count: number) => string;
}

/**
 * 三个维度投影成同一行形状。点击写入的筛选按维度不同：模型行同时写供应商，
 * 因为跨供应商的同名模型只有配上供应商才唯一。
 */
function buildRows(
  dim: BreakdownDim,
  summary: UsageSummary | null,
  filters: UsageRecordsFilters,
  labels: RowLabels,
): BreakdownRowView[] {
  if (!summary) return [];
  const breakdown = summary.breakdown[dim];
  const rows: BreakdownRowView[] = [];

  if (dim === "project") {
    for (const row of summary.breakdown.project.rows) {
      rows.push({
        key: `project:${row.project_name}`,
        name: labels.projectLabel(row.project_name),
        sub: null,
        stats: row,
        active: filters.project === row.project_name,
        filters:
          filters.project === row.project_name
            ? { project: null }
            : { project: row.project_name },
      });
    }
  } else if (dim === "provider") {
    for (const row of summary.breakdown.provider.rows) {
      const active = filters.provider === row.provider;
      rows.push({
        key: `provider:${row.provider}`,
        name: labels.providerLabel(row.provider),
        sub: null,
        stats: row,
        active,
        filters: active
          ? { provider: null, model: null }
          : { provider: row.provider, model: null },
      });
    }
  } else {
    for (const row of summary.breakdown.model.rows) {
      const active = filters.provider === row.provider && filters.model === row.model;
      rows.push({
        key: `model:${row.provider}/${row.model}`,
        name: row.model,
        sub: labels.providerLabel(row.provider),
        stats: row,
        active,
        // 取消只撤回模型：供应商是可以单独成立的筛选，留着它由用户自己的 chip 清除。
        filters: active ? { model: null } : { provider: row.provider, model: row.model },
      });
    }
  }

  if (breakdown.other) {
    rows.push({
      key: "other",
      name: labels.other(breakdown.other.groups),
      sub: null,
      stats: breakdown.other,
      active: false,
      filters: null,
    });
  }
  return rows;
}
