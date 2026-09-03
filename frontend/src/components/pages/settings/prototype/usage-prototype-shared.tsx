// PROTOTYPE — wayfinder #2290 共享零件：URL 筛选、媒体类型字形、状态 pill、记录行（表格态 / 紧凑态）。
// 记录行有意做成一个组件两种布局，用来回答「顶栏悬浮层能否直接复用这行」。评审后整目录删除。
import { useEffect, useState, type ReactNode } from "react";
import { useLocation, useSearch } from "wouter";
import { AlertTriangle, ChevronDown, Film, Image as ImageIcon, Mic, Type, X } from "lucide-react";

import { useNowTick } from "@/hooks/useNowTick";

import {
  ERROR_LABELS,
  EMPTY_FILTERS,
  MEDIA_LABELS,
  PURPOSE_LABELS,
  durationLabel,
  money,
  projectLabel,
  providerLabel,
  shortTime,
  type ActiveRow,
  type ActiveTask,
  type Filters,
  type MediaType,
  type RecordStatus,
  type UsageRecord,
} from "./usage-prototype-data";

// ---------------------------------------------------------------------------
// URL 筛选（写入 query，刷新可复现，供顶栏「查看全部记录」预填）
// ---------------------------------------------------------------------------

const KEYS: Array<keyof Filters> = ["project", "provider", "model", "media", "status", "range", "segment"];

export function useProtoFilters(): [Filters, (patch: Partial<Filters>) => void] {
  const [location, navigate] = useLocation();
  const search = useSearch();
  const p = new URLSearchParams(search);
  const f: Filters = { ...EMPTY_FILTERS };
  for (const k of KEYS) {
    const v = p.get(`u_${k}`);
    if (v === null) continue;
    if (k === "range") f.range = Number(v);
    else (f as unknown as Record<string, unknown>)[k] = v;
  }
  const set = (patch: Partial<Filters>) => {
    const q = new URLSearchParams(search);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === undefined || (k === "range" && v === 30)) q.delete(`u_${k}`);
      else q.set(`u_${k}`, String(v));
    }
    navigate(`${location}?${q.toString()}`, { replace: true });
  };
  return [f, set];
}

export function activeFilterChips(f: Filters): Array<{ key: keyof Filters; label: string }> {
  const chips: Array<{ key: keyof Filters; label: string }> = [];
  if (f.project !== null) chips.push({ key: "project", label: projectLabel(f.project) });
  if (f.provider) chips.push({ key: "provider", label: providerLabel(f.provider) });
  if (f.model) chips.push({ key: "model", label: f.model });
  if (f.media) chips.push({ key: "media", label: MEDIA_LABELS[f.media] });
  if (f.status) chips.push({ key: "status", label: STATUS_LABELS[f.status] });
  if (f.segment) chips.push({ key: "segment", label: `分镜 ${f.segment}` });
  return chips;
}

// ---------------------------------------------------------------------------
// 字形与 pill
// ---------------------------------------------------------------------------

/** 媒体类型色：只用于字形与图表标记，文字一律用文本 token。顺序 image/audio/video/text 通过 dataviz 校验器（暗面 CVD ΔE≥15）。 */
export const MEDIA_TONE: Record<MediaType, string> = {
  image: "#248fcc",
  audio: "#c48225",
  video: "#9565c7",
  text: "#339c6d",
};

const MEDIA_ICON: Record<MediaType, typeof Film> = { image: ImageIcon, video: Film, text: Type, audio: Mic };

export function MediaGlyph({ type, size = 13 }: { type: MediaType; size?: number }) {
  const Icon = MEDIA_ICON[type];
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-[5px]"
      style={{ width: size + 9, height: size + 9, background: `${MEDIA_TONE[type]}26`, color: MEDIA_TONE[type] }}
      title={MEDIA_LABELS[type]}
    >
      <Icon style={{ width: size, height: size }} strokeWidth={1.8} />
    </span>
  );
}

export const STATUS_LABELS: Record<RecordStatus | "queued" | "running" | "cancelling", string> = {
  pending: "进行中",
  queued: "排队中",
  running: "生成中",
  cancelling: "取消中",
  success: "成功",
  failed: "失败",
  cancelled: "已取消",
};

const STATUS_TONE: Record<keyof typeof STATUS_LABELS, { fg: string; dot: string; pulse?: boolean }> = {
  pending: { fg: "var(--color-accent-2)", dot: "var(--color-accent)", pulse: true },
  running: { fg: "var(--color-accent-2)", dot: "var(--color-accent)", pulse: true },
  queued: { fg: "var(--color-text-3)", dot: "var(--color-text-4)", pulse: true },
  cancelling: { fg: "var(--color-warm)", dot: "var(--color-warm)", pulse: true },
  success: { fg: "var(--color-text-3)", dot: "var(--color-good)" },
  failed: { fg: "var(--color-danger-2)", dot: "var(--color-danger)" },
  cancelled: { fg: "var(--color-text-4)", dot: "var(--color-text-4)" },
};

export function StatusPill({ status, compact }: { status: keyof typeof STATUS_LABELS; compact?: boolean }) {
  const t = STATUS_TONE[status];
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-[11px]" style={{ color: t.fg }}>
      <span
        aria-hidden
        className={"h-[6px] w-[6px] rounded-full" + (t.pulse ? " animate-breathe" : "")}
        style={{ background: t.dot, boxShadow: t.pulse ? `0 0 0 3px ${t.dot}33` : undefined }}
      />
      {!compact && STATUS_LABELS[status]}
    </span>
  );
}

// ---------------------------------------------------------------------------
// 记录行：一个组件、两种布局
// ---------------------------------------------------------------------------

export type RowLayout = "table" | "compact";

/** 表格态列宽（grid-template-columns），表头与行共用。 */
export const ROW_GRID = "28px minmax(0,1fr) minmax(0,1.2fr) minmax(0,1.2fr) 72px 86px 78px 20px";

export function RowHeader() {
  const cell = "font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-text-4";
  return (
    <div className="grid items-center gap-x-3 border-b border-hairline px-3 pb-2" style={{ gridTemplateColumns: ROW_GRID }}>
      <span />
      <span className={cell}>Project</span>
      <span className={cell}>Target</span>
      <span className={cell}>Model</span>
      <span className={cell}>Status</span>
      <span className={cell + " text-right"}>Time</span>
      <span className={cell + " text-right"}>Ref. cost</span>
      <span />
    </div>
  );
}

function targetOf(r: UsageRecord): string {
  if (r.media_type === "text") return PURPOSE_LABELS[r.purpose];
  if (r.purpose === "endpoint_trial") return "端点试跑";
  return r.segment_id ? `分镜 ${r.segment_id}` : "—";
}

function Elapsed({ from }: { from: string }) {
  const now = useNowTick();
  return <span className="num tabular-nums">{durationLabel(now - new Date(from).getTime())}</span>;
}

interface RecordRowProps {
  record: UsageRecord;
  layout: RowLayout;
  /** 当前筛选已固定项目时，行内可隐藏项目列（顶栏悬浮层始终隐藏）。 */
  hideProject?: boolean;
  onLocate?: (segment: string) => void;
}

export function RecordRow({ record: r, layout, hideProject, onLocate }: RecordRowProps) {
  const [open, setOpen] = useState(false);
  const failed = r.status === "failed";
  const pending = r.status === "pending";
  const cost = r.cost_amount > 0 ? money(r.currency, r.cost_amount, r.cost_amount < 0.1 ? 3 : 2) : "—";
  const modelText = `${providerLabel(r.provider)} · ${r.model}`;
  const error = failed ? (r.error_code ? ERROR_LABELS[r.error_code] : r.error_message) : null;

  if (layout === "compact") {
    return (
      <div className={"rounded-[8px] px-2.5 py-2 transition-colors hover:bg-bg-grad-a/70" + (failed ? " bg-[oklch(0.30_0.10_25/0.14)]" : "")}>
        <div className="flex items-center gap-2.5">
          <MediaGlyph type={r.media_type} size={12} />
          <span className="min-w-0 flex-1 truncate text-[12.5px] text-text">{hideProject ? targetOf(r) : `${projectLabel(r.project_name)} · ${targetOf(r)}`}</span>
          <StatusPill status={r.status} compact />
          <span className="num shrink-0 text-[12px] text-text-2">{pending ? <Elapsed from={r.started_at} /> : cost}</span>
        </div>
        <div className="mt-0.5 flex items-center gap-2 pl-[31px] text-[11px] text-text-4">
          <span className="truncate">{modelText}</span>
          <span className="ml-auto shrink-0 num">{shortTime(r.started_at)}</span>
        </div>
        {error && <div className="mt-1 pl-[31px] text-[11px] leading-[1.45] text-danger-2">{error}</div>}
      </div>
    );
  }

  return (
    <div className={"border-b border-hairline-soft last:border-b-0" + (failed ? " bg-[oklch(0.30_0.10_25/0.10)]" : "")}>
      <button
        type="button"
        onClick={() => failed && setOpen((v) => !v)}
        aria-expanded={failed ? open : undefined}
        className={"grid w-full items-center gap-x-3 px-3 py-2 text-left transition-colors hover:bg-bg-grad-a/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent" + (failed ? " cursor-pointer" : " cursor-default")}
        style={{ gridTemplateColumns: ROW_GRID }}
      >
        <MediaGlyph type={r.media_type} />
        <span className="truncate text-[12.5px] text-text-2">{hideProject ? "" : projectLabel(r.project_name)}</span>
        <span className="truncate text-[12.5px] text-text">{targetOf(r)}</span>
        <span className="truncate text-[12px] text-text-3">{modelText}</span>
        <StatusPill status={r.status} />
        <span className="num text-right text-[11.5px] text-text-3">{pending ? <Elapsed from={r.started_at} /> : shortTime(r.started_at)}</span>
        <span className="num text-right text-[12.5px] text-text">{cost}</span>
        <span className="flex justify-end text-text-4">{failed && <ChevronDown className={"h-3.5 w-3.5 transition-transform" + (open ? " rotate-180" : "")} />}</span>
      </button>
      {failed && open && (
        <div className="grid gap-x-3 px-3 pb-3 pt-0.5" style={{ gridTemplateColumns: ROW_GRID }}>
          <span />
          <div className="col-span-6 rounded-[8px] border border-danger/25 bg-[oklch(0.30_0.10_25/0.14)] px-3 py-2.5 text-[12px] leading-[1.55]">
            <div className="text-danger-2">{error}</div>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-text-4">
              <span className="num">{durationLabel(r.duration_ms)} 后失败</span>
              <span className="num">#{r.id}</span>
              {r.segment_id && onLocate && (
                <button type="button" className="text-accent-2 hover:underline" onClick={() => onLocate(r.segment_id!)}>
                  只看该分镜
                </button>
              )}
              <span className="ml-auto truncate font-mono text-text-4" title={r.error_message ?? ""}>{r.error_message}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 进行中行：任务（语义同 ADR 0006：取消 / 重试下载 / 警示）
// ---------------------------------------------------------------------------

function taskLabel(t: ActiveTask) {
  return `分镜 ${t.segment_id}`;
}

export function ActiveTaskRow({ task: t, layout, hideProject }: { task: ActiveTask; layout: RowLayout; hideProject?: boolean }) {
  const modelText = t.model ? `${providerLabel(t.provider)} · ${t.model}` : `${providerLabel(t.provider)} · 待解析`;
  const started = t.started_at ?? t.queued_at;
  const cancel = (
    <button type="button" className="rounded-[5px] p-0.5 text-text-4 transition-colors hover:bg-bg-grad-a hover:text-danger-2" aria-label="取消任务" title="取消">
      <X className="h-3.5 w-3.5" />
    </button>
  );
  const warn = t.warnings > 0 && (
    <span className="inline-flex items-center gap-1 text-[11px] text-warm" title={`${t.warnings} 条警示`}>
      <AlertTriangle className="h-3 w-3" />
      {t.warnings}
    </span>
  );
  if (layout === "compact") {
    return (
      <div className="relative overflow-hidden rounded-[8px] px-2.5 py-2">
        <div className="flex items-center gap-2.5">
          <MediaGlyph type={t.media_type} size={12} />
          <span className="min-w-0 flex-1 truncate text-[12.5px] text-text">{hideProject ? taskLabel(t) : `${t.project_name} · ${taskLabel(t)}`}</span>
          {warn}
          <StatusPill status={t.status} compact />
          <span className="num shrink-0 text-[12px] text-text-2">
            <Elapsed from={started} />
          </span>
          {cancel}
        </div>
        <div className="mt-0.5 flex items-center gap-2 pl-[31px] text-[11px] text-text-4">
          <span className="truncate">{modelText}</span>
          <span className="ml-auto shrink-0">{STATUS_LABELS[t.status]}</span>
        </div>
        {t.status === "running" && <RunningBar />}
      </div>
    );
  }
  return (
    <div className="relative overflow-hidden border-b border-hairline-soft last:border-b-0">
      <div className="grid w-full items-center gap-x-3 px-3 py-2" style={{ gridTemplateColumns: ROW_GRID }}>
        <MediaGlyph type={t.media_type} />
        <span className="truncate text-[12.5px] text-text-2">{hideProject ? "" : t.project_name}</span>
        <span className="flex items-center gap-2 truncate text-[12.5px] text-text">
          {taskLabel(t)}
          {warn}
        </span>
        <span className="truncate text-[12px] text-text-3">{modelText}</span>
        <StatusPill status={t.status} />
        <span className="num text-right text-[11.5px] text-text-2">
          <Elapsed from={started} />
        </span>
        <span className="num text-right text-[12.5px] text-text-4">—</span>
        <span className="flex justify-end">{cancel}</span>
      </div>
      {t.status === "running" && <RunningBar />}
    </div>
  );
}

function RunningBar() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-px overflow-hidden">
      <div className="animate-progress-pulse h-full w-1/3" style={{ background: "linear-gradient(90deg, transparent, var(--color-accent), transparent)" }} />
    </div>
  );
}

export function ActiveRows({ rows, layout, hideProject, onLocate }: { rows: ActiveRow[]; layout: RowLayout; hideProject?: boolean; onLocate?: (s: string) => void }) {
  return (
    <>
      {rows.map((row) =>
        row.kind === "task" ? (
          <ActiveTaskRow key={row.task.task_id} task={row.task} layout={layout} hideProject={hideProject} />
        ) : (
          <RecordRow key={row.record.id} record={row.record} layout={layout} hideProject={hideProject} onLocate={onLocate} />
        ),
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// 小件
// ---------------------------------------------------------------------------

export function Kicker({ children, tone = "accent" }: { children: ReactNode; tone?: "accent" | "muted" }) {
  return (
    <div className={"font-mono text-[10px] font-bold uppercase tracking-[0.16em] " + (tone === "accent" ? "text-accent-2" : "text-text-4")}>{children}</div>
  );
}

export function Seg<T extends string | number>({ value, options, onChange, size = "sm" }: { value: T; options: Array<{ value: T; label: string }>; onChange: (v: T) => void; size?: "sm" | "xs" }) {
  return (
    <div className="inline-flex rounded-[7px] border border-hairline bg-bg-grad-b/60 p-0.5" role="tablist">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(o.value)}
            className={
              "rounded-[5px] px-2.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent " +
              (size === "xs" ? "py-0.5 text-[11px] " : "py-1 text-[12px] ") +
              (active ? "bg-surface-2 text-text shadow-[inset_0_1px_0_oklch(1_0_0/0.06)]" : "text-text-3 hover:text-text-2")
            }
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** 顶栏悬浮层宽度（26rem）下的行复用预览，挂在每个变体末尾。 */
export function CompactPreview({ active, records, project }: { active: ActiveRow[]; records: UsageRecord[]; project: string }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "p" && !(document.activeElement instanceof HTMLInputElement)) setOpen((v) => !v);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  if (!open)
    return (
      <button type="button" onClick={() => setOpen(true)} className="fixed bottom-5 right-5 z-40 rounded-full border border-hairline bg-surface-2 px-3 py-1.5 font-mono text-[10.5px] text-text-3 shadow-lg hover:text-text">
        行复用预览 · 26rem（P）
      </button>
    );
  return (
    <div className="fixed bottom-5 right-5 z-40 w-[26rem] rounded-[12px] border border-hairline p-3 shadow-2xl shadow-black/60" style={{ background: "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.96), oklch(0.16 0.010 265 / 0.96))", backdropFilter: "blur(12px)" }}>
      <div className="mb-2 flex items-center justify-between">
        <Kicker>Row reuse · {project}</Kicker>
        <button type="button" onClick={() => setOpen(false)} className="text-text-4 hover:text-text" aria-label="关闭">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <ActiveRows rows={active.filter((r) => (r.kind === "task" ? r.task.project_name : r.record.project_name) === project)} layout="compact" hideProject />
      <div className="my-1.5 border-t border-hairline-soft" />
      {records
        .filter((r) => r.project_name === project)
        .slice(0, 5)
        .map((r) => (
          <RecordRow key={r.id} record={r} layout="compact" hideProject />
        ))}
    </div>
  );
}
