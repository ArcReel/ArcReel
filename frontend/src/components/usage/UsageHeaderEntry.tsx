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

/** 无使用记录时也占住同样的宽度：第一笔参考费用出现时顶栏不能抖动。 */
const FALLBACK_CURRENCY = "USD";

/**
 * 顶栏用量入口。按钮上是本项目全部时间的参考费用（多币种全列，主币种正常字号），
 * 有任务运行或排队时图标呼吸并挂计数角标。点开是同一份数据的悬浮层。
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

  // 打开即取一轮：面板平时靠事件刷新，打开这一刻拿到的应当是最新的。
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
