import { useState } from "react";
import { useTranslation } from "react-i18next";

import { CARD_STYLE } from "@/components/ui/darkroom-tokens";
import type { UsageStatsBlock, UsageSummary } from "@/types";
import type { UsageRecordsFilters } from "@/stores/usage-records-store";
import { costEntries, formatCurrencyAmount } from "@/utils/cost-format";
import { formatRatio, providerLabelResolver } from "./usage-record-format";

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

const GRID = "minmax(0,1fr) 52px 56px 84px";

const HEAD_CLS = "font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-text-4";

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
  /** 需要关注隐藏时构成表铺满整行。 */
  wide: boolean;
}

export function UsageBreakdownCard({
  summary,
  filters,
  onChange,
  wide,
}: UsageBreakdownCardProps) {
  const { t } = useTranslation("dashboard");
  const [dim, setDim] = useState<BreakdownDim>("provider");
  const providerLabel = providerLabelResolver(summary);
  const primary = summary?.primary_currency ?? null;

  const rows = buildRows(dim, summary, filters, {
    providerLabel,
    untitled: t("usage_project_untitled"),
    other: (count: number) => t("usage_breakdown_other", { count }),
  });
  const totalCalls = Math.max(1, summary?.kpi.calls ?? 0);

  return (
    <section
      className={
        "rounded-[10px] border border-hairline p-4 " + (wide ? "col-span-12" : "col-span-12 lg:col-span-7")
      }
      style={CARD_STYLE}
    >
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <h4 className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
          {t("usage_breakdown_title")}
        </h4>
        <div
          role="group"
          aria-label={t("usage_breakdown_dim_label")}
          className="flex items-center gap-1"
        >
          {DIMS.map((option) => {
            const active = dim === option.value;
            return (
              <button
                key={option.value}
                type="button"
                aria-pressed={active}
                onClick={() => setDim(option.value)}
                className={
                  "focus-ring rounded-full px-2 py-0.5 text-[11px] transition-colors " +
                  (active ? "bg-accent-dim text-accent-2" : "text-text-3 hover:text-text")
                }
              >
                {t(option.labelKey)}
              </button>
            );
          })}
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="py-6 text-center text-[12px] text-text-3">{t("usage_breakdown_empty")}</p>
      ) : (
        <>
          <div
            className="grid gap-x-2 border-b border-hairline pb-1.5"
            style={{ gridTemplateColumns: GRID }}
          >
            <span className={HEAD_CLS}>{t(NAME_COL_KEYS[dim])}</span>
            <span className={`${HEAD_CLS} text-right`}>{t("usage_breakdown_col_calls")}</span>
            <span className={`${HEAD_CLS} text-right`}>{t("usage_kpi_success_rate")}</span>
            <span className={`${HEAD_CLS} text-right`}>{t("usage_col_cost")}</span>
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
        </>
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
  const content = (
    <>
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-1 left-0 rounded-r-[2px] bg-accent/10"
        style={{ width: `${share * 100}%` }}
      />
      <span className="relative min-w-0 truncate text-left text-[12.5px]">
        {row.name}
        {row.sub && <span className="ml-1.5 text-[11px] text-text-4">{row.sub}</span>}
      </span>
      <span className="num relative text-right text-[12px]">{row.stats.calls}</span>
      <span
        className={
          "num relative text-right text-[12px] " +
          (row.stats.success_rate !== null && row.stats.success_rate < LOW_SUCCESS_RATE
            ? "text-danger-2"
            : "")
        }
      >
        {formatRatio(row.stats.success_rate)}
      </span>
      <CostCell cost={row.stats.cost} primary={primary} />
    </>
  );

  const shared =
    "relative grid w-full items-center gap-x-2 border-b border-hairline-soft py-1.5 last:border-b-0";

  const patch = row.filters;
  if (patch === null) {
    return (
      <div className={`${shared} text-text-3`} style={{ gridTemplateColumns: GRID }}>
        {content}
      </div>
    );
  }

  return (
    <button
      type="button"
      aria-pressed={row.active}
      onClick={() => onChange(patch)}
      className={
        `${shared} focus-ring transition-colors hover:bg-bg-grad-a/60 ` +
        (row.active ? "text-text" : "text-text-2")
      }
      style={{ gridTemplateColumns: GRID }}
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
    <span className="num relative text-right text-[12px]">
      {main}
      {others.length > 0 && (
        <>
          <span aria-hidden="true" className="ml-1 text-[10px] text-text-4">
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
  untitled: string;
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
        name: row.project_name || labels.untitled,
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
