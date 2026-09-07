import { useEffect, useId, useRef } from "react";
import { Activity } from "lucide-react";
import { useTranslation } from "react-i18next";

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
  const anchorRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

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
    <div className="relative" ref={anchorRef}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={label}
        title={label}
        className="focus-ring relative inline-flex h-[30px] items-center gap-1.5 rounded-md px-2 text-[11.5px] transition-colors"
        style={{
          background: open ? "var(--color-accent-dim)" : "oklch(0.22 0.011 265 / 0.5)",
          border: `1px solid ${
            activeCount > 0 ? "var(--color-accent-soft)" : "var(--color-hairline-soft)"
          }`,
          color: "var(--color-text-2)",
        }}
      >
        <Activity
          aria-hidden="true"
          className={"h-3.5 w-3.5" + (activeCount > 0 ? " animate-breathe" : "")}
          style={{ color: activeCount > 0 ? "var(--color-accent-2)" : "var(--color-text-3)" }}
        />
        <span className="num font-medium">{primaryText}</span>
        {others.map(([currency, amount]) => (
          <span key={currency} className="num text-[10.5px] text-text-4">
            {formatCurrencyAmount(currency, amount)}
          </span>
        ))}
        {activeCount > 0 && (
          <span
            className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold"
            style={{ background: "var(--color-accent)", color: "oklch(0.14 0 0)" }}
          >
            {activeCount > 9 ? "9+" : activeCount}
          </span>
        )}
      </button>
      <UsagePopover projectName={projectName} anchorRef={anchorRef} panelId={panelId} />
    </div>
  );
}
