// PROTOTYPE — wayfinder #2290 变体 B「记录优先 · 分面栏」：记录表是主角，构成表变成左侧分面导航（点击即筛选），
// KPI 压成一行、趋势图压成 72px 的小条、需要关注以横幅插在表格上方。栏宽放开到 max-w-6xl。评审后整目录删除。
import { useState, type CSSProperties } from "react";
import { Check } from "lucide-react";

import { CARD_STYLE } from "@/components/ui/darkroom-tokens";

import { AttentionList, RANGE_OPTIONS, RecordsTable } from "./usage-prototype-blocks";
import { MEDIA_LABELS, activeRows, computeSummary, filterTerminal, moneyAll, pct, projectLabel, providerLabel, type BreakdownRow, type Filters, type MediaType, type RecordStatus } from "./usage-prototype-data";
import { CompactPreview, Kicker, MEDIA_TONE, STATUS_LABELS, Seg, activeFilterChips, useProtoFilters } from "./usage-prototype-shared";
import { UsageTrendChart, isWeekly, type TrendMetric } from "./UsageTrendChart";

const EDITORIAL: CSSProperties = { fontWeight: 400, fontSize: 22, lineHeight: 1.1, letterSpacing: "-0.012em" };

function Facet({ title, rows, name, active, onPick, max }: { title: string; rows: BreakdownRow[]; name: (r: BreakdownRow) => string; active: (r: BreakdownRow) => boolean; onPick: (r: BreakdownRow) => void; max: number }) {
  return (
    <div>
      <Kicker tone="muted">{title}</Kicker>
      <ul className="mt-1.5 flex flex-col">
        {rows.map((r) => {
          const on = active(r);
          const low = r.success_rate !== null && r.success_rate < 0.85;
          return (
            <li key={name(r)}>
              <button
                type="button"
                onClick={() => onPick(r)}
                aria-pressed={on}
                className={"relative flex w-full items-center gap-2 rounded-[6px] px-2 py-1 text-left text-[12px] transition-colors hover:bg-bg-grad-a/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent " + (on ? "bg-accent-dim text-text" : "text-text-2")}
              >
                <span aria-hidden className="pointer-events-none absolute inset-y-1 left-0 rounded-r-[2px] bg-accent/10" style={{ width: `${(r.calls / max) * 100}%` }} />
                <span className="relative min-w-0 flex-1 truncate">{name(r)}</span>
                {low && <span aria-hidden className="relative h-[5px] w-[5px] rounded-full bg-danger" title={`成功率 ${pct(r.success_rate)}`} />}
                <span className="num relative text-[11px] text-text-4">{r.calls}</span>
                {on && <Check className="relative h-3 w-3 text-accent-2" />}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function UsagePrototypeB() {
  const [f, set] = useProtoFilters();
  const [metric, setMetric] = useState<TrendMetric>("calls");
  const s = computeSummary(f);
  const cost = moneyAll(s.kpi.cost, s.primary_currency);
  const weekly = isWeekly(s.daily);
  const chips = activeFilterChips(f);
  const maxOf = (rows: BreakdownRow[]) => Math.max(1, ...rows.map((r) => r.calls));
  const mediaRows: BreakdownRow[] = (Object.keys(MEDIA_LABELS) as MediaType[]).map((m) => {
    const xs = filterTerminal({ ...f, media: m, status: null }, false);
    const success = xs.filter((r) => r.status === "success").length;
    const failed = xs.filter((r) => r.status === "failed").length;
    return { model: m, calls: xs.length, success, failed, cancelled: xs.length - success - failed, success_rate: success + failed ? success / (success + failed) : null, cost: {} };
  });
  const toggle = (patch: Partial<Filters>, on: boolean) => set(on ? Object.fromEntries(Object.keys(patch).map((k) => [k, null])) : patch);

  return (
    <div className="mx-auto grid max-w-6xl gap-6 px-8 py-8" style={{ gridTemplateColumns: "196px minmax(0,1fr)" }}>
      {/* Facet rail */}
      <aside className="sticky top-8 flex h-fit flex-col gap-5">
        <div>
          <Kicker tone="muted">Range</Kicker>
          <ul className="mt-1.5 flex flex-col">
            {RANGE_OPTIONS.map((o) => {
              const on = f.range === o.value;
              return (
                <li key={o.value}>
                  <button type="button" onClick={() => set({ range: o.value })} className={"flex w-full items-center gap-2 rounded-[6px] px-2 py-1 text-left text-[12px] hover:bg-bg-grad-a/70 " + (on ? "bg-accent-dim text-text" : "text-text-2")}>
                    <span className="flex-1">{o.label}</span>
                    {on && <Check className="h-3 w-3 text-accent-2" />}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
        <Facet title="Project" rows={s.breakdown.project} max={maxOf(s.breakdown.project)} name={(r) => projectLabel(r.project_name!)} active={(r) => f.project === r.project_name} onPick={(r) => toggle({ project: r.project_name! }, f.project === r.project_name)} />
        <Facet title="Provider" rows={s.breakdown.provider} max={maxOf(s.breakdown.provider)} name={(r) => providerLabel(r.provider!)} active={(r) => f.provider === r.provider} onPick={(r) => (f.provider === r.provider ? set({ provider: null, model: null }) : set({ provider: r.provider!, model: null }))} />
        <Facet title="Model" rows={s.breakdown.model} max={maxOf(s.breakdown.model)} name={(r) => r.model!} active={(r) => f.model === r.model} onPick={(r) => (f.model === r.model ? set({ model: null }) : set({ provider: r.provider!, model: r.model! }))} />
        <Facet title="Media" rows={mediaRows} max={maxOf(mediaRows)} name={(r) => MEDIA_LABELS[r.model as MediaType]} active={(r) => f.media === r.model} onPick={(r) => toggle({ media: r.model as MediaType }, f.media === r.model)} />
      </aside>

      <div className="min-w-0 space-y-5">
        <div className="flex items-end justify-between gap-4">
          <div>
            <Kicker>Usage Records</Kicker>
            <h3 className="font-editorial mt-1" style={EDITORIAL}>使用记录</h3>
          </div>
          {chips.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 pb-1 text-[11px] text-text-4">
              筛选：
              {chips.map((c) => (
                <button key={c.key} type="button" onClick={() => set({ [c.key]: null })} className="rounded-full border border-accent/40 bg-accent-dim px-2 py-0.5 text-accent-2">{c.label} ×</button>
              ))}
            </div>
          )}
        </div>

        {/* Health strip: KPI 一行 + 小趋势条 */}
        <section className="rounded-[10px] border border-hairline px-4 pt-3 pb-2" style={CARD_STYLE}>
          <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
            {[
              ["调用", s.kpi.calls.toLocaleString()],
              ["成功率", pct(s.kpi.success_rate)],
              ["失败", String(s.kpi.failed), s.kpi.failed > 0],
              ["参考费用", cost.main + (cost.others.length ? ` · ${cost.others.join(" · ")}` : "")],
            ].map(([label, value, danger]) => (
              <span key={label as string} className="inline-flex items-baseline gap-1.5">
                <span className="text-[11px] text-text-4">{label}</span>
                <span className={"num text-[15px] " + (danger ? "text-danger-2" : "text-text")}>{value}</span>
              </span>
            ))}
            <span className="ml-auto inline-flex items-center gap-2">
              {metric === "cost" && s.primary_currency && (
                <span className="rounded-full border border-hairline px-1.5 py-px font-mono text-[10px] text-text-4" title={cost.others.length ? `${cost.others.join("、")} 未计入` : "单一币种"}>
                  {s.primary_currency}{cost.others.length ? ` +${cost.others.length}` : ""}
                </span>
              )}
              <span className="font-mono text-[10px] text-text-4">{weekly ? "按周" : "按天"}</span>
              <Seg size="xs" value={metric} onChange={setMetric} options={[{ value: "calls", label: "次数" }, { value: "cost", label: "费用" }]} />
            </span>
          </div>
          <div className="mt-2">
            <UsageTrendChart daily={s.daily} metric={metric} primaryCurrency={s.primary_currency} height={84} />
          </div>
        </section>

        <AttentionList items={s.attention} set={set} layout="banner" />

        <section className="rounded-[10px] border border-hairline p-4" style={CARD_STYLE}>
          <div className="mb-3 flex items-center justify-between gap-3">
            <Kicker>Records</Kicker>
            <Seg size="xs" value={f.status ?? "*"} onChange={(v) => set({ status: v === "*" ? null : (v) })} options={[{ value: "*", label: "全部" }, ...(["pending", "success", "failed", "cancelled"] as RecordStatus[]).map((st) => ({ value: st, label: STATUS_LABELS[st] }))]} />
          </div>
          <RecordsTable f={f} set={set} pageSize={30} />
        </section>

        <div className="flex items-center gap-3 text-[11px] text-text-4">
          <span>类型色：</span>
          {(Object.keys(MEDIA_LABELS) as MediaType[]).map((m) => (
            <span key={m} className="inline-flex items-center gap-1"><span aria-hidden className="h-[8px] w-[8px] rounded-[2px]" style={{ background: MEDIA_TONE[m] }} />{MEDIA_LABELS[m]}</span>
          ))}
        </div>
      </div>

      <CompactPreview active={activeRows({ ...f, status: null })} records={filterTerminal({ ...f, status: null, range: 0 }, false)} project="星海列车" />
    </div>
  );
}
