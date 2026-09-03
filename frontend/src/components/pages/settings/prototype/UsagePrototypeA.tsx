// PROTOTYPE — wayfinder #2290 变体 A「纵向仪表盘」：五个区块自上而下按 KPI → 趋势 → 构成 | 关注 → 记录 排布，
// 沿用设置页 max-w-4xl 栏宽；主币种标注写在费用 KPI 格与趋势图页脚。评审后整目录删除。
import { useState, type CSSProperties } from "react";

import { CARD_STYLE } from "@/components/ui/darkroom-tokens";

import { AttentionList, BreakdownTable, FilterRow, RecordsTable } from "./usage-prototype-blocks";
import { activeRows, computeSummary, filterTerminal, moneyAll, pct, type RecordStatus } from "./usage-prototype-data";
import { CompactPreview, Kicker, STATUS_LABELS, Seg, useProtoFilters } from "./usage-prototype-shared";
import { TrendLegend, UsageTrendChart, isWeekly, type TrendMetric } from "./UsageTrendChart";

const EDITORIAL: CSSProperties = { fontWeight: 400, fontSize: 22, lineHeight: 1.1, letterSpacing: "-0.012em" };
const KPI: CSSProperties = { fontSize: 24, fontWeight: 400, letterSpacing: "-0.01em", lineHeight: 1.1 };

export function UsagePrototypeA() {
  const [f, set] = useProtoFilters();
  const [metric, setMetric] = useState<TrendMetric>("calls");
  const s = computeSummary(f);
  const cost = moneyAll(s.kpi.cost, s.primary_currency);
  const weekly = isWeekly(s.daily);
  const otherNote = cost.others.length > 0 ? `${cost.others.join("、")} 未计入` : null;

  return (
    <div className="mx-auto max-w-4xl space-y-7 px-8 py-8">
      <div>
        <Kicker>Usage Records</Kicker>
        <h3 className="font-editorial mt-1" style={EDITORIAL}>使用记录</h3>
        <p className="mt-1.5 text-[12.5px] leading-[1.6] text-text-3">每一行是一次供应商调用；费用按你配置的单价估算，只作参考。</p>
      </div>

      <FilterRow f={f} set={set} />

      {/* KPI strip */}
      <div className="grid grid-cols-4 overflow-hidden rounded-[10px] border border-hairline" style={CARD_STYLE}>
        {[
          { label: "Calls", value: s.kpi.calls.toLocaleString(), sub: `${s.range ? `${s.range.since.slice(5).replace("-", "/")} – ${s.range.until.slice(5).replace("-", "/")}` : "—"}` },
          { label: "Success rate", value: pct(s.kpi.success_rate), sub: `${s.kpi.success} 成功` },
          { label: "Failed", value: s.kpi.failed.toLocaleString(), sub: s.kpi.cancelled ? `另 ${s.kpi.cancelled} 次取消` : "无取消", danger: s.kpi.failed > 0 },
          { label: `Ref. cost${s.primary_currency ? ` · ${s.primary_currency}` : ""}`, value: cost.main, sub: cost.others.length ? `+ ${cost.others.join(" + ")}` : "单一币种" },
        ].map((k, i) => (
          <div key={k.label} className={"px-5 py-4" + (i > 0 ? " border-l border-hairline-soft" : "")}>
            <Kicker tone="muted">{k.label}</Kicker>
            <div className={"font-editorial mt-1 " + (k.danger ? "text-danger-2" : "text-text")} style={KPI}>{k.value}</div>
            <div className="num mt-1 text-[11px] text-text-4">{k.sub}</div>
          </div>
        ))}
      </div>

      {/* Trend */}
      <section className="rounded-[10px] border border-hairline p-4" style={CARD_STYLE}>
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <Kicker>Trend</Kicker>
            <div className="mt-1 flex items-center gap-2 text-[13.5px] text-text">
              {metric === "calls" ? "调用次数" : "参考费用"}
              <span className="rounded-full border border-hairline px-1.5 py-px font-mono text-[10px] text-text-4">{weekly ? "按周合并" : "按天"}</span>
            </div>
          </div>
          <Seg size="xs" value={metric} onChange={setMetric} options={[{ value: "calls", label: "调用次数" }, { value: "cost", label: "参考费用" }]} />
        </div>
        <UsageTrendChart daily={s.daily} metric={metric} primaryCurrency={s.primary_currency} height={200} />
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <TrendLegend metric={metric} />
          {metric === "cost" && s.primary_currency && (
            <span className="text-[11px] text-text-4">
              只按 {s.primary_currency} 作图{otherNote ? `，${otherNote}` : ""}
            </span>
          )}
        </div>
      </section>

      {/* Breakdown | Attention */}
      <div className={"grid gap-5 " + (s.attention.length ? "grid-cols-12" : "grid-cols-1")}>
        <section className={"rounded-[10px] border border-hairline p-4 " + (s.attention.length ? "col-span-7" : "")} style={CARD_STYLE}>
          <BreakdownTable summary={s} f={f} set={set} dense={s.attention.length > 0} />
        </section>
        {s.attention.length > 0 && (
          <section className="col-span-5 rounded-[10px] border border-danger/20 p-4" style={CARD_STYLE}>
            <div className="mb-2.5 flex items-center justify-between">
              <Kicker>Needs attention</Kicker>
              <span className="num text-[11px] text-text-4">{s.attention.length}</span>
            </div>
            <AttentionList items={s.attention} set={set} />
          </section>
        )}
      </div>

      {/* Records */}
      <section className="rounded-[10px] border border-hairline p-4" style={CARD_STYLE}>
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <Kicker>Records</Kicker>
            <div className="mt-1 text-[13.5px] text-text">调用记录</div>
          </div>
          <Seg
            size="xs"
            value={f.status ?? "*"}
            onChange={(v) => set({ status: v === "*" ? null : (v) })}
            options={[{ value: "*", label: "全部" }, ...(["pending", "success", "failed", "cancelled"] as RecordStatus[]).map((st) => ({ value: st, label: STATUS_LABELS[st] }))]}
          />
        </div>
        <RecordsTable f={f} set={set} />
      </section>

      <CompactPreview active={activeRows({ ...f, status: null })} records={filterTerminal({ ...f, status: null, range: 0 }, false)} project="星海列车" />
    </div>
  );
}
