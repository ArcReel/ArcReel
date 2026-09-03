// PROTOTYPE — wayfinder #2290 变体 C「概览 / 记录 分页签」：把分析（KPI 主数字、需要关注、趋势、三维构成并排）
// 与流水（记录表）拆成两个页签；需要关注放在概览最顶端，点击跳到记录页签并预填筛选。评审后整目录删除。
import { useState, type CSSProperties } from "react";

import { CARD_STYLE } from "@/components/ui/darkroom-tokens";

import { AttentionList, FilterRow, RecordsTable } from "./usage-prototype-blocks";
import { activeRows, computeSummary, filterTerminal, moneyAll, pct, projectLabel, providerLabel, type BreakdownRow, type Filters, type Summary } from "./usage-prototype-data";
import { CompactPreview, Kicker, Seg, useProtoFilters } from "./usage-prototype-shared";
import { TrendLegend, UsageTrendChart, isWeekly, type TrendMetric } from "./UsageTrendChart";

const EDITORIAL: CSSProperties = { fontWeight: 400, fontSize: 22, lineHeight: 1.1, letterSpacing: "-0.012em" };
const HERO: CSSProperties = { fontSize: 52, fontWeight: 400, letterSpacing: "-0.02em", lineHeight: 1 };

type Tab = "overview" | "records";

function MiniBreakdown({ title, rows, name, sub, active, onPick, primary }: { title: string; rows: BreakdownRow[]; name: (r: BreakdownRow) => string; sub?: (r: BreakdownRow) => string | null; active: (r: BreakdownRow) => boolean; onPick: (r: BreakdownRow) => void; primary: string | null }) {
  const max = Math.max(1, ...rows.map((r) => r.calls));
  return (
    <div className="min-w-0">
      <Kicker tone="muted">{title}</Kicker>
      <ul className="mt-2 flex flex-col gap-0.5">
        {rows.slice(0, 6).map((r) => {
          const on = active(r);
          const low = r.success_rate !== null && r.success_rate < 0.85;
          const c = moneyAll(r.cost, primary);
          return (
            <li key={name(r)}>
              <button type="button" onClick={() => onPick(r)} aria-pressed={on} className={"block w-full rounded-[6px] px-2 py-1.5 text-left transition-colors hover:bg-bg-grad-a/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent " + (on ? "bg-accent-dim" : "")}>
                <span className="flex items-center justify-between gap-2 text-[12px]">
                  <span className={"min-w-0 truncate " + (on ? "text-text" : "text-text-2")}>
                    {name(r)}
                    {sub?.(r) && <span className="ml-1 text-[10.5px] text-text-4">{sub(r)}</span>}
                  </span>
                  <span className="num shrink-0 text-text-3">{c.main}{c.others.length ? <span className="text-text-4"> +</span> : null}</span>
                </span>
                <span className="mt-1 flex items-center gap-2">
                  <span className="h-[3px] flex-1 overflow-hidden rounded-full bg-bg-grad-b">
                    <span className="block h-full rounded-full" style={{ width: `${(r.calls / max) * 100}%`, background: low ? "var(--color-danger)" : "var(--color-accent)" }} />
                  </span>
                  <span className={"num text-[10.5px] " + (low ? "text-danger-2" : "text-text-4")}>{r.calls} · {pct(r.success_rate)}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Overview({ s, f, set, goRecords }: { s: Summary; f: Filters; set: (p: Partial<Filters>) => void; goRecords: (p: Partial<Filters>) => void }) {
  const [metric, setMetric] = useState<TrendMetric>("calls");
  const cost = moneyAll(s.kpi.cost, s.primary_currency);
  const weekly = isWeekly(s.daily);
  return (
    <div className="space-y-6">
      {s.attention.length > 0 && (
        <section>
          <div className="mb-2 flex items-center gap-2">
            <Kicker>Needs attention</Kicker>
            <span className="num text-[11px] text-text-4">{s.attention.length}</span>
          </div>
          <AttentionList items={s.attention} set={goRecords} />
        </section>
      )}

      {/* Hero figures */}
      <section className="grid grid-cols-12 items-end gap-6 rounded-[10px] border border-hairline px-6 py-5" style={CARD_STYLE}>
        <div className="col-span-5">
          <Kicker tone="muted">Calls</Kicker>
          <div className="font-editorial mt-2 text-text" style={HERO}>{s.kpi.calls.toLocaleString()}</div>
          <div className="mt-2 text-[12px] text-text-3">
            {s.kpi.success} 成功 · <span className={s.kpi.failed ? "text-danger-2" : ""}>{s.kpi.failed} 失败</span>{s.kpi.cancelled ? ` · ${s.kpi.cancelled} 取消` : ""}
          </div>
        </div>
        <div className="col-span-3 border-l border-hairline-soft pl-6">
          <Kicker tone="muted">Success rate</Kicker>
          <div className="font-editorial mt-2 text-text" style={{ ...HERO, fontSize: 34 }}>{pct(s.kpi.success_rate)}</div>
          <div className="mt-2 text-[12px] text-text-4">成功 ÷（成功 + 失败）</div>
        </div>
        <div className="col-span-4 border-l border-hairline-soft pl-6">
          <Kicker tone="muted">Ref. cost</Kicker>
          <div className="font-editorial mt-2 text-text" style={{ ...HERO, fontSize: 34 }}>{cost.main}</div>
          <div className="mt-2 text-[12px] text-text-4">{cost.others.length ? <>另计 <span className="num text-text-3">{cost.others.join("、")}</span>，不折算</> : "按你配置的单价估算"}</div>
        </div>
      </section>

      <section className="rounded-[10px] border border-hairline p-4" style={CARD_STYLE}>
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Kicker>Trend</Kicker>
            <TrendLegend metric={metric} />
            {metric === "cost" && s.primary_currency && <span className="text-[11px] text-text-4">· 仅 {s.primary_currency}</span>}
          </div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] text-text-4">{weekly ? `按周合并 · ${s.daily.length} 天` : "按天"}</span>
            <Seg size="xs" value={metric} onChange={setMetric} options={[{ value: "calls", label: "调用次数" }, { value: "cost", label: "参考费用" }]} />
          </div>
        </div>
        <UsageTrendChart daily={s.daily} metric={metric} primaryCurrency={s.primary_currency} height={240} />
      </section>

      <section className="rounded-[10px] border border-hairline p-4" style={CARD_STYLE}>
        <div className="mb-1 flex items-center justify-between">
          <Kicker>Breakdown</Kicker>
          <span className="text-[11px] text-text-4">点击任一行写入筛选，三列同时收窄</span>
        </div>
        <div className="grid grid-cols-3 gap-6">
          <MiniBreakdown title="Project" rows={s.breakdown.project} primary={s.primary_currency} name={(r) => projectLabel(r.project_name!)} active={(r) => f.project === r.project_name} onPick={(r) => set({ project: f.project === r.project_name ? null : r.project_name! })} />
          <MiniBreakdown title="Provider" rows={s.breakdown.provider} primary={s.primary_currency} name={(r) => providerLabel(r.provider!)} active={(r) => f.provider === r.provider} onPick={(r) => set(f.provider === r.provider ? { provider: null, model: null } : { provider: r.provider!, model: null })} />
          <MiniBreakdown title="Model" rows={s.breakdown.model} primary={s.primary_currency} name={(r) => r.model!} sub={(r) => providerLabel(r.provider!)} active={(r) => f.model === r.model} onPick={(r) => set(f.model === r.model ? { model: null } : { provider: r.provider!, model: r.model! })} />
        </div>
      </section>
    </div>
  );
}

export function UsagePrototypeC() {
  const [f, set] = useProtoFilters();
  const [tab, setTab] = useState<Tab>("overview");
  const s = computeSummary(f);
  const goRecords = (p: Partial<Filters>) => {
    set(p);
    setTab("records");
  };
  return (
    <div className="mx-auto max-w-4xl space-y-6 px-8 py-8">
      <div className="flex items-end justify-between gap-4">
        <div>
          <Kicker>Usage Records</Kicker>
          <h3 className="font-editorial mt-1" style={EDITORIAL}>使用记录</h3>
        </div>
        <Seg value={tab} onChange={setTab} options={[{ value: "overview", label: "概览" }, { value: "records", label: `记录 · ${filterTerminal(f, true).length}` }]} />
      </div>
      <FilterRow f={f} set={set} showStatus={tab === "records"} />
      {tab === "overview" ? (
        <Overview s={s} f={f} set={set} goRecords={goRecords} />
      ) : (
        <section className="rounded-[10px] border border-hairline p-4" style={CARD_STYLE}>
          <RecordsTable f={f} set={set} pageSize={30} />
        </section>
      )}
      <CompactPreview active={activeRows({ ...f, status: null })} records={filterTerminal({ ...f, status: null, range: 0 }, false)} project="星海列车" />
    </div>
  );
}
