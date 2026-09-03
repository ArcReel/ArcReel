// PROTOTYPE — wayfinder #2290 区块级零件：筛选行、构成表、需要关注、记录表。变体 A / C 直接组合；变体 B 自带分面栏只借记录表。
import { useState } from "react";
import { AlertOctagon, Repeat2, RefreshCw, X } from "lucide-react";

import {
  FILTER_OPTIONS,
  MEDIA_LABELS,
  PROVIDER_LABELS,
  activeRows,
  filterTerminal,
  moneyAll,
  pct,
  projectLabel,
  providerLabel,
  shortTime,
  type AttentionItem,
  type BreakdownRow,
  type Filters,
  type MediaType,
  type RecordStatus,
  type Summary,
} from "./usage-prototype-data";
import { ActiveRows, Kicker, RecordRow, RowHeader, STATUS_LABELS, Seg, activeFilterChips } from "./usage-prototype-shared";

export const RANGE_OPTIONS = [
  { value: 7, label: "7 天" },
  { value: 30, label: "30 天" },
  { value: 90, label: "90 天" },
  { value: 0, label: "全部" },
];

const SELECT_CLS =
  "rounded-[7px] border border-hairline bg-bg-grad-a/55 px-2 py-1 text-[12px] text-text-2 transition-colors hover:border-hairline-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

/** 一行筛选：时间范围在最左，其后是维度下拉；已生效筛选以 chip 形式可单个撤销。 */
export function FilterRow({ f, set, showStatus }: { f: Filters; set: (p: Partial<Filters>) => void; showStatus?: boolean }) {
  const chips = activeFilterChips(f);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Seg value={f.range} options={RANGE_OPTIONS} onChange={(range) => set({ range })} />
      <select className={SELECT_CLS} value={f.project ?? "*"} onChange={(e) => set({ project: e.target.value === "*" ? null : e.target.value })} aria-label="项目">
        <option value="*">全部项目</option>
        {FILTER_OPTIONS.projects.map((p) => (
          <option key={p} value={p}>{projectLabel(p)}</option>
        ))}
        <option value="">未命名（端点试跑）</option>
      </select>
      <select className={SELECT_CLS} value={f.provider ?? "*"} onChange={(e) => set({ provider: e.target.value === "*" ? null : e.target.value, model: null })} aria-label="供应商">
        <option value="*">全部供应商</option>
        {FILTER_OPTIONS.providers.map((p) => (
          <option key={p} value={p}>{PROVIDER_LABELS[p]}</option>
        ))}
      </select>
      <select className={SELECT_CLS} value={f.model ?? "*"} onChange={(e) => set({ model: e.target.value === "*" ? null : e.target.value })} aria-label="模型">
        <option value="*">全部模型</option>
        {FILTER_OPTIONS.models.filter((m) => !f.provider || m.provider === f.provider).map((m) => (
          <option key={m.model} value={m.model}>{m.model}</option>
        ))}
      </select>
      <select className={SELECT_CLS} value={f.media ?? "*"} onChange={(e) => set({ media: e.target.value === "*" ? null : (e.target.value as MediaType) })} aria-label="媒体类型">
        <option value="*">全部类型</option>
        {(Object.keys(MEDIA_LABELS) as MediaType[]).map((m) => (
          <option key={m} value={m}>{MEDIA_LABELS[m]}</option>
        ))}
      </select>
      {showStatus && (
        <select className={SELECT_CLS} value={f.status ?? "*"} onChange={(e) => set({ status: e.target.value === "*" ? null : (e.target.value as RecordStatus) })} aria-label="状态">
          <option value="*">全部状态</option>
          {(["pending", "success", "failed", "cancelled"] as RecordStatus[]).map((s) => (
            <option key={s} value={s}>{STATUS_LABELS[s]}</option>
          ))}
        </select>
      )}
      {chips.length > 0 && (
        <span className="ml-1 flex flex-wrap items-center gap-1.5">
          {chips.map((c) => (
            <button key={c.key} type="button" onClick={() => set({ [c.key]: null })} className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent-dim px-2 py-0.5 text-[11px] text-accent-2 hover:border-accent/70">
              {c.label}
              <X className="h-3 w-3" />
            </button>
          ))}
          <button type="button" onClick={() => set({ project: null, provider: null, model: null, media: null, status: null, segment: null })} className="text-[11px] text-text-4 hover:text-text-2">
            清除
          </button>
        </span>
      )}
      <button type="button" className="ml-auto inline-flex items-center gap-1 text-[11.5px] text-text-4 hover:text-text-2" title="重新取数">
        <RefreshCw className="h-3 w-3" />
        刷新
      </button>
    </div>
  );
}

export type BreakdownDim = "project" | "provider" | "model";

/** 构成表：一维一次只展示一张表，行点击把该维度写入筛选。 */
export function BreakdownTable({ summary, f, set, dense }: { summary: Summary; f: Filters; set: (p: Partial<Filters>) => void; dense?: boolean }) {
  const [dim, setDim] = useState<BreakdownDim>("provider");
  const rows = summary.breakdown[dim];
  const primary = summary.primary_currency;
  const nameOf = (r: BreakdownRow) => (dim === "project" ? projectLabel(r.project_name!) : dim === "provider" ? providerLabel(r.provider!) : r.model!);
  const subOf = (r: BreakdownRow) => (dim === "model" ? providerLabel(r.provider!) : null);
  const isActive = (r: BreakdownRow) => (dim === "project" ? f.project === r.project_name : dim === "provider" ? f.provider === r.provider : f.model === r.model);
  const apply = (r: BreakdownRow) => {
    if (isActive(r)) return set(dim === "project" ? { project: null } : dim === "provider" ? { provider: null, model: null } : { model: null });
    set(dim === "project" ? { project: r.project_name! } : dim === "provider" ? { provider: r.provider!, model: null } : { provider: r.provider!, model: r.model! });
  };
  const maxCalls = Math.max(1, ...rows.map((r) => r.calls));
  const cell = "font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-text-4";
  const grid = dense ? "minmax(0,1fr) 44px 48px 72px" : "minmax(0,1fr) 56px 60px 88px";
  return (
    <div>
      <div className="mb-2.5 flex items-center justify-between">
        <Kicker>Breakdown</Kicker>
        <Seg size="xs" value={dim} onChange={setDim} options={[{ value: "project", label: "项目" }, { value: "provider", label: "供应商" }, { value: "model", label: "模型" }]} />
      </div>
      <div className="grid gap-x-2 border-b border-hairline pb-1.5" style={{ gridTemplateColumns: grid }}>
        <span className={cell}>{dim === "project" ? "Project" : dim === "provider" ? "Provider" : "Model"}</span>
        <span className={cell + " text-right"}>Calls</span>
        <span className={cell + " text-right"}>OK</span>
        <span className={cell + " text-right"}>Cost</span>
      </div>
      {rows.map((r) => {
        const active = isActive(r);
        const cost = moneyAll(r.cost, primary);
        const lowRate = r.success_rate !== null && r.success_rate < 0.85;
        return (
          <button
            key={nameOf(r) + (r.provider ?? "")}
            type="button"
            onClick={() => apply(r)}
            aria-pressed={active}
            className={"relative grid w-full items-center gap-x-2 border-b border-hairline-soft py-1.5 text-left transition-colors last:border-b-0 hover:bg-bg-grad-a/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent " + (active ? "text-text" : "text-text-2")}
            style={{ gridTemplateColumns: grid }}
          >
            <span aria-hidden className="pointer-events-none absolute inset-y-1 left-0 rounded-r-[2px] bg-accent/10" style={{ width: `${(r.calls / maxCalls) * 100}%` }} />
            <span className="relative min-w-0 truncate text-[12.5px]">
              {active && <span aria-hidden className="mr-1.5 inline-block h-[5px] w-[5px] rounded-full bg-accent align-middle" />}
              {nameOf(r)}
              {subOf(r) && <span className="ml-1.5 text-[11px] text-text-4">{subOf(r)}</span>}
            </span>
            <span className="num relative text-right text-[12px]">{r.calls}</span>
            <span className={"num relative text-right text-[12px] " + (lowRate ? "text-danger-2" : "")}>{pct(r.success_rate)}</span>
            <span className="num relative text-right text-[12px]">
              {cost.main}
              {cost.others.length > 0 && <span className="ml-1 text-[10px] text-text-4">+{cost.others.length === 1 ? cost.others[0].slice(0, 1) : cost.others.length}</span>}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** 需要关注：只列两类异常，为空时整块不渲染由调用方决定。 */
export function AttentionList({ items, set, layout = "list" }: { items: AttentionItem[]; set: (p: Partial<Filters>) => void; layout?: "list" | "banner" }) {
  if (items.length === 0) return null;
  return (
    <div className={layout === "banner" ? "flex flex-col gap-1.5" : "flex flex-col gap-2"}>
      {items.map((it, i) => {
        const isRate = it.type === "failure_rate";
        const title = isRate ? `${providerLabel(it.provider)}${it.model ? ` · ${it.model}` : ""} 失败率偏高` : `${projectLabel(it.project_name)} · 分镜 ${it.segment_id} 连续失败`;
        const detail = isRate ? `${it.failed} 次失败 / ${it.success + it.failed} 次 · ${pct(it.failure_rate)}，期间总体 ${pct(it.overall_failure_rate)}` : `${it.count} 次（${MEDIA_LABELS[it.media_type]}），最近 ${shortTime(it.last_failed_at)}`;
        const go = () => (isRate ? set({ provider: it.provider, model: it.model, status: "failed" }) : set({ project: it.project_name, media: it.media_type, segment: it.segment_id, status: null }));
        const Icon = isRate ? AlertOctagon : Repeat2;
        return (
          <button
            key={i}
            type="button"
            onClick={go}
            className={
              "group flex w-full items-start gap-2.5 rounded-[8px] border text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent " +
              (layout === "banner" ? "border-danger/25 bg-[oklch(0.30_0.10_25/0.12)] px-3 py-2 hover:border-danger/45" : "border-hairline bg-bg-grad-b/40 px-3 py-2.5 hover:border-danger/40")
            }
          >
            <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />
            <span className="min-w-0 flex-1">
              <span className="block text-[12.5px] text-text">{title}</span>
              <span className="mt-0.5 block text-[11.5px] text-text-3">{detail}</span>
            </span>
            <span className="shrink-0 pt-0.5 text-[11px] text-text-4 group-hover:text-accent-2">{isRate ? "看失败记录" : "看该分镜"}</span>
          </button>
        );
      })}
    </div>
  );
}

/** 记录表：进行中区置顶（不受时间范围），已结束按时间倒序，分页用「加载更多」。 */
export function RecordsTable({ f, set, pageSize = 20, header = true }: { f: Filters; set: (p: Partial<Filters>) => void; pageSize?: number; header?: boolean }) {
  const [limit, setLimit] = useState(pageSize);
  const active = activeRows(f);
  const rows = filterTerminal(f, true);
  const locate = (segment: string) => set({ segment, status: null });
  const hideProject = f.project !== null;
  return (
    <div>
      {header && <RowHeader />}
      {active.length > 0 && (
        <div className="border-b border-hairline bg-accent-dim/30">
          <div className="flex items-center gap-2 px-3 pt-2 pb-1">
            <Kicker>In progress · {active.length}</Kicker>
            <button type="button" className="ml-auto text-[11px] text-text-4 hover:text-danger-2">全部取消</button>
          </div>
          <ActiveRows rows={active} layout="table" hideProject={hideProject} onLocate={locate} />
        </div>
      )}
      {rows.slice(0, limit).map((r) => (
        <RecordRow key={r.id} record={r} layout="table" hideProject={hideProject} onLocate={locate} />
      ))}
      {rows.length === 0 && <div className="px-3 py-8 text-center text-[12.5px] text-text-3">这段时间没有符合筛选的记录</div>}
      <div className="flex items-center justify-between px-3 pt-3 text-[11.5px] text-text-4">
        <span className="num">
          {Math.min(limit, rows.length)} / {rows.length}
        </span>
        {rows.length > limit && (
          <button type="button" onClick={() => setLimit((v) => v + pageSize)} className="text-accent-2 hover:underline">
            加载更多
          </button>
        )}
      </div>
    </div>
  );
}
