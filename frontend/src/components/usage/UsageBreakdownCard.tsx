import { useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import type { UsageStatsBlock, UsageSummary } from "@/types";
import { viewPatch } from "@/stores/usage-records-store";
import type { UsageRecordsFilters } from "@/stores/usage-records-store";
import { costEntries, formatCurrencyAmount } from "@/utils/cost-format";
import { formatRatio, projectTitleResolver, providerLabelResolver, usageProjectLabel } from "./usage-record-format";

/** Three breakdown dimensions; model rows group by (provider, model). */
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

/** Rows below this success-rate threshold use warning colors for easy scanning. */
const LOW_SUCCESS_RATE = 0.85;

/** Name columns take remaining width; three numeric columns have fixed widths. */
const GRID_CLS = "grid grid-cols-[minmax(0,1fr)_3.5rem_4rem_6rem] items-center gap-x-2";

/** Display-row data; dimensions project into shared shapes/rendering. */
interface BreakdownRowView {
  key: string;
  name: string;
  /** Model-row subtitles identify providers. */
  sub: string | null;
  stats: UsageStatsBlock;
  /** `other` rows are unclickable without corresponding filter values. */
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
    agentSessions: t("usage_breakdown_agent_sessions"),
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
      {/* Share bars: row calls/total calls determine widths through CSS variables. */}
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
      <CostCell cost={row.stats.cost} unpricedCalls={row.stats.unpriced_calls} primary={primary} />
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

function CostCell({
  cost,
  unpricedCalls,
  primary,
}: {
  cost: Record<string, number>;
  unpricedCalls: number;
  primary: string | null;
}) {
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
      {unpricedCalls > 0 && (
        <span className="block text-xs text-muted-foreground">
          {t("usage_breakdown_unpriced", { count: unpricedCalls })}
        </span>
      )}
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
  /** Names of merged Agent-record entries. */
  agentSessions: string;
}

/**
 * Three dimensions project into shared rows. Clicked filter writes depend on dimensions: model rows also write providers,
 * since cross-provider same-named models are unique only with providers.
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
      if (row.agent) {
        // Clicks switch views; provider-filtered Agent records appear only in Agent sessions views.
        const active = filters.view === "agent";
        rows.push({
          key: "provider:agent",
          name: labels.agentSessions,
          sub: null,
          stats: row,
          active,
          filters: viewPatch(active ? "generation" : "agent"),
        });
        continue;
      }
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
      if (row.agent) {
        const active = filters.view === "agent" && filters.model === row.model;
        rows.push({
          key: `model:agent/${row.model}`,
          name: row.model,
          sub: labels.agentSessions,
          stats: row,
          active,
          filters: active ? { model: null } : { ...viewPatch("agent"), model: row.model },
        });
        continue;
      }
      const active = filters.provider === row.provider && filters.model === row.model;
      rows.push({
        key: `model:${row.provider}/${row.model}`,
        name: row.model,
        sub: labels.providerLabel(row.provider),
        stats: row,
        active,
        // Deselecting clears models only; providers are independently valid filters, cleared by their own chips.
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
