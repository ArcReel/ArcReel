import { useEffect } from "react";
import { Activity } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Popover, PopoverTrigger } from "@/components/ui/popover";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageHeaderStore } from "@/stores/usage-header-store";
import { costEntries, formatCurrencyAmount } from "@/utils/cost-format";
import { UsagePopover } from "./UsagePopover";

/** Reserve the same width even without usage records so the first reference cost cannot shift the header. */
const FALLBACK_CURRENCY = "USD";

/**
 * Header usage entry. The button shows all-time project reference costs, listing every currency with the primary one at normal font size;
 * while tasks run or queue, pulse the icon and show a count badge. Open an overlay of the same data on click.
 */
export function UsageHeaderEntry({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");

  const open = useAppStore((s) => s.usagePanelOpen);
  const setOpen = useAppStore((s) => s.setUsagePanelOpen);
  const summary = useUsageHeaderStore((s) => s.summary);
  const refresh = useUsageHeaderStore((s) => s.refresh);
  const running = useTasksStore((s) => s.stats.running);
  const queued = useTasksStore((s) => s.stats.queued);
  const activeCount = running + queued;

  // Fetch once on opening: events normally refresh the panel, but it must show current data at opening time.
  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  const cost = summary?.kpi.cost ?? {};
  const primaryCurrency = summary?.primary_currency ?? null;
  const entries = costEntries(cost);
  const primaryEntry = primaryCurrency
    ? entries.find(([currency]) => currency === primaryCurrency)
    : undefined;
  const others = entries.filter(([currency]) => currency !== primaryEntry?.[0]);
  const primaryText = primaryEntry
    ? formatCurrencyAmount(primaryEntry[0], primaryEntry[1])
    : formatCurrencyAmount(primaryCurrency ?? FALLBACK_CURRENCY, 0);
  const fullCost = [
    primaryText,
    ...others.map(([currency, amount]) => formatCurrencyAmount(currency, amount)),
  ].join(" + ");
  const label =
    activeCount > 0
      ? t("usage_entry_aria_active", { cost: fullCost, count: activeCount })
      : t("usage_entry_aria", { cost: fullCost });

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button variant="outline" size="sm" aria-label={label} className="relative" />
        }
      >
        <Activity
          aria-hidden="true"
          data-icon="inline-start"
          className={activeCount > 0 ? "animate-breath text-primary" : "text-muted-foreground"}
        />
        <span className="num">{primaryText}</span>
        {others.map(([currency, amount]) => (
          <span key={currency} className="num text-xs font-normal text-muted-foreground">
            {formatCurrencyAmount(currency, amount)}
          </span>
        ))}
        {activeCount > 0 && (
          <span
            aria-hidden="true"
            className="absolute -top-1.5 -right-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-xs font-medium text-primary-foreground"
          >
            {activeCount > 9 ? "9+" : activeCount}
          </span>
        )}
      </PopoverTrigger>
      <UsagePopover projectName={projectName} />
    </Popover>
  );
}
