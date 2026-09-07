// PROTOTYPE — wayfinder #2291 顶栏入口按钮与精简悬浮层。
// DEV 且 URL 带 ?ue=<scenario> 时，GlobalHeader 用它替换现有「费用明细」与「任务雷达」两个按钮：
//   ?variant=A|B|C   悬浮层结构（A 时间线 26rem / B 摘要句 + 直播卡 28rem / C 宽版双栏 40rem），入口按钮随变体变化
//   ?ue=busy|active|done|empty   假数据场景（进行中 + 已结束 / 只有进行中 / 只有已结束 / 什么都没有）
// 假数据取 #2290 原型的「星海列车」项目；取消 / 重试下载只改本地状态。评审后整目录删除。
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useLocation, useSearch } from "wouter";
import { Activity, AlertTriangle, ArrowUpRight, Loader2, RotateCcw, X } from "lucide-react";

import { GlassPopover } from "@/components/ui/GlassPopover";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import { useNowTick } from "@/hooks/useNowTick";
import { PrototypeSwitcher } from "@/components/pages/settings/prototype/PrototypeSwitcher";
import { RecordDetailHost } from "@/components/pages/settings/prototype/UsageRecordDetail";
import {
  Kicker,
  MediaGlyph,
  STATUS_LABELS,
  StatusPill,
  useRecordParam,
} from "@/components/pages/settings/prototype/usage-prototype-shared";
import {
  ACTIVE_TASKS,
  ERROR_LABELS,
  NOW,
  PURPOSE_LABELS,
  RECORDS,
  durationLabel,
  money,
  pct,
  providerLabel,
  shortTime,
  type ActiveTask,
  type UsageRecord,
} from "@/components/pages/settings/prototype/usage-prototype-data";

// ---------------------------------------------------------------------------
// 场景与假数据
// ---------------------------------------------------------------------------

const PROJECT = "星海列车";

export type Scenario = "busy" | "active" | "done" | "empty";
const SCENARIOS: Array<{ key: Scenario; name: string; hint: string }> = [
  { key: "busy", name: "进行中 + 已结束", hint: "1" },
  { key: "active", name: "只有进行中", hint: "2" },
  { key: "done", name: "只有已结束", hint: "3" },
  { key: "empty", name: "没有任何调用", hint: "4" },
];

const VARIANTS = [
  { key: "A", name: "时间线 · 26rem" },
  { key: "B", name: "摘要句 + 直播卡 · 28rem" },
  { key: "C", name: "宽版双栏 · 40rem" },
];

const ago = (ms: number) => new Date(NOW.getTime() - ms).toISOString();

/** 本项目额外的排队 / 取消中任务：让「全部取消」「待解析」「取消中」三种状态都能在悬浮层里看到。 */
const EXTRA_TASKS: ActiveTask[] = [
  {
    task_id: "t_q_a",
    project_name: PROJECT,
    media_type: "video",
    task_type: "video_generation",
    provider: "minimax",
    model: null,
    status: "queued",
    segment_id: "E1S14",
    queued_at: ago(8_000),
    started_at: null,
    warnings: 0,
  },
  {
    task_id: "t_q_b",
    project_name: PROJECT,
    media_type: "audio",
    task_type: "audio_generation",
    provider: "elevenlabs",
    model: "eleven_v3",
    status: "queued",
    segment_id: "E1S12",
    queued_at: ago(5_000),
    started_at: null,
    warnings: 0,
  },
  {
    task_id: "t_cancelling",
    project_name: PROJECT,
    media_type: "image",
    task_type: "image_generation",
    provider: "volcengine",
    model: "seedream-4.0",
    status: "cancelling",
    segment_id: "E1S15",
    queued_at: ago(60_000),
    started_at: ago(40_000),
    warnings: 0,
  },
];

const WARNINGS_BY_TASK: Record<string, string[]> = {
  t_run2: ["参考图分辨率低于 1024px，供应商已自动放大"],
};

/** 最近已结束里塞两条特定失败：下载失败（可重试下载）与内容策略（原因短语）。 */
const EXTRA_RECORDS: UsageRecord[] = [
  {
    id: 9901,
    project_name: PROJECT,
    purpose: "generation_task",
    task_id: "t_dl",
    task_type: "video_generation",
    media_type: "video",
    provider: "google",
    model: "veo-3",
    status: "failed",
    error_code: "download_failed",
    error_message: "ECONNRESET while downloading result",
    segment_id: "E1S11",
    output_path: null,
    started_at: ago(12 * 60_000),
    finished_at: ago(9 * 60_000),
    duration_ms: 180_000,
    cost_amount: 0.75,
    currency: "USD",
    input_tokens: null,
    output_tokens: null,
  },
  {
    id: 9902,
    project_name: PROJECT,
    purpose: "generation_task",
    task_id: "t_cp",
    task_type: "image_generation",
    media_type: "image",
    provider: "volcengine",
    model: "seedream-4.0",
    status: "failed",
    error_code: "content_policy",
    error_message: 'HTTP 400: {"code":"content_policy"}',
    segment_id: "E1S10",
    output_path: null,
    started_at: ago(25 * 60_000),
    finished_at: ago(25 * 60_000 - 6_000),
    duration_ms: 6_000,
    cost_amount: 0.2,
    currency: "CNY",
    input_tokens: null,
    output_tokens: null,
  },
];

type HudActive = { kind: "task"; task: ActiveTask; at: string } | { kind: "call"; record: UsageRecord; at: string };

function baseActive(): HudActive[] {
  const rows: HudActive[] = [];
  for (const t of [...ACTIVE_TASKS.filter((t) => t.project_name === PROJECT), ...EXTRA_TASKS])
    rows.push({ kind: "task", task: t, at: t.started_at ?? t.queued_at });
  for (const r of RECORDS)
    if (r.status === "pending" && !r.task_id && r.project_name === PROJECT) rows.push({ kind: "call", record: r, at: r.started_at });
  return rows.sort((a, b) => (a.at < b.at ? 1 : -1));
}

function baseRecent(): UsageRecord[] {
  return [...EXTRA_RECORDS, ...RECORDS.filter((r) => r.project_name === PROJECT && r.status !== "pending")].sort((a, b) =>
    a.started_at < b.started_at ? 1 : -1,
  );
}

interface Kpi {
  calls: number;
  success: number;
  failed: number;
  cancelled: number;
  success_rate: number | null;
  cost: Record<string, number>;
  primary: string | null;
}

function kpiOf(records: UsageRecord[]): Kpi {
  const k: Kpi = { calls: 0, success: 0, failed: 0, cancelled: 0, success_rate: null, cost: {}, primary: null };
  for (const r of records) {
    k.calls++;
    if (r.status === "success") k.success++;
    else if (r.status === "failed") k.failed++;
    else if (r.status === "cancelled") k.cancelled++;
    if (r.cost_amount > 0) k.cost[r.currency] = (k.cost[r.currency] ?? 0) + r.cost_amount;
  }
  const denom = k.success + k.failed;
  k.success_rate = denom === 0 ? null : k.success / denom;
  const entries = Object.entries(k.cost).sort((a, b) => b[1] - a[1] || (a[0] === "USD" ? -1 : 1));
  k.primary = entries[0]?.[0] ?? null;
  return k;
}

function costParts(k: Kpi): { main: string | null; others: string[] } {
  const entries = Object.entries(k.cost).filter(([, v]) => v > 0);
  if (entries.length === 0) return { main: null, others: [] };
  const main = entries.find(([c]) => c === k.primary) ?? entries[0];
  return { main: money(main[0], main[1]), others: entries.filter((e) => e !== main).map(([c, v]) => money(c, v)) };
}

/** 场景数据 + 本地可变状态（取消 / 重试下载只改这里）。 */
function useScenarioData(scenario: Scenario) {
  const [active, setActive] = useState<HudActive[]>([]);
  const [recent, setRecent] = useState<UsageRecord[]>([]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 原型：切换场景时重置假数据
    setActive(scenario === "busy" || scenario === "active" ? baseActive() : []);
    setRecent(scenario === "busy" || scenario === "done" ? baseRecent() : []);
  }, [scenario]);

  const kpi = useMemo(() => kpiOf(recent), [recent]);
  const running = active.filter((a) => a.kind === "call" || a.task.status !== "queued").length;
  const queued = active.filter((a) => a.kind === "task" && a.task.status === "queued").length;

  const cancelTask = useCallback((taskId: string) => {
    setActive((rows) =>
      rows.map((row) => (row.kind === "task" && row.task.task_id === taskId ? { ...row, task: { ...row.task, status: "cancelling" } } : row)),
    );
    window.setTimeout(() => setActive((rows) => rows.filter((row) => !(row.kind === "task" && row.task.task_id === taskId))), 1400);
  }, []);
  const cancelQueued = useCallback(() => {
    setActive((rows) => rows.filter((row) => !(row.kind === "task" && row.task.status === "queued")));
  }, []);
  const [retrying, setRetrying] = useState<ReadonlySet<number>>(new Set());
  const retryDownload = useCallback((id: number) => {
    setRetrying((s) => new Set(s).add(id));
    window.setTimeout(() => {
      setRetrying((s) => {
        const n = new Set(s);
        n.delete(id);
        return n;
      });
      setRecent((rows) =>
        rows.map((r) =>
          r.id === id ? { ...r, status: "success", error_code: null, error_message: null, output_path: `${PROJECT}/${r.segment_id}.mp4` } : r,
        ),
      );
    }, 1500);
  }, []);

  return { active, recent, kpi, running, queued, cancelTask, cancelQueued, retryDownload, retrying };
}

type ScenarioData = ReturnType<typeof useScenarioData>;

// ---------------------------------------------------------------------------
// 小件
// ---------------------------------------------------------------------------

function Elapsed({ from }: { from: string }) {
  const now = useNowTick();
  return <span className="num tabular-nums">{durationLabel(Math.max(0, now - Date.parse(from)))}</span>;
}

const ERROR_SHORT: Record<string, string> = {
  provider_rate_limited: "限流",
  content_policy: "内容策略",
  provider_timeout: "超时",
  download_failed: "下载失败",
};

function targetOf(r: UsageRecord): string {
  if (r.media_type === "text") return PURPOSE_LABELS[r.purpose];
  if (r.purpose === "endpoint_trial") return "端点试跑";
  return r.segment_id ? `分镜 ${r.segment_id}` : "—";
}

function RunningBar() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-px overflow-hidden">
      <div className="animate-progress-pulse h-full w-1/3" style={{ background: "linear-gradient(90deg, transparent, var(--color-accent), transparent)" }} />
    </div>
  );
}

const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

function IconAction({ label, onClick, danger, children }: { label: string; onClick: () => void; danger?: boolean; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className={"shrink-0 rounded-[5px] p-0.5 text-text-4 transition-colors hover:bg-bg-grad-a " + (danger ? "hover:text-danger-2 " : "hover:text-accent-2 ") + FOCUS}
      aria-label={label}
      title={label}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// 行：紧凑态（#2290 的 compact 布局 + 取消 / 重试下载 / 警示展开）
// ---------------------------------------------------------------------------

function HudTaskRow({ task: t, onCancel }: { task: ActiveTask; onCancel: (id: string) => void }) {
  const [showWarnings, setShowWarnings] = useState(false);
  const modelText = t.model ? `${providerLabel(t.provider)} · ${t.model}` : `${providerLabel(t.provider)} · 待解析`;
  const started = t.started_at ?? t.queued_at;
  const warnings = WARNINGS_BY_TASK[t.task_id] ?? [];
  return (
    <div className="relative overflow-hidden rounded-[8px] px-2.5 py-2">
      <div className="flex items-center gap-2.5">
        <MediaGlyph type={t.media_type} size={12} />
        <span className="min-w-0 flex-1 truncate text-[12.5px] text-text">分镜 {t.segment_id}</span>
        {t.warnings > 0 && (
          <button
            type="button"
            onClick={() => setShowWarnings((v) => !v)}
            aria-expanded={showWarnings}
            className={"inline-flex items-center gap-1 rounded px-1 text-[11px] text-warm transition-colors hover:bg-warm-soft " + FOCUS}
            title={`${t.warnings} 条警示`}
          >
            <AlertTriangle className="h-3 w-3" />
            <span className="num">{t.warnings}</span>
          </button>
        )}
        <StatusPill status={t.status} compact />
        <span className="num w-[52px] shrink-0 text-right text-[12px] text-text-2">
          <Elapsed from={started} />
        </span>
        {t.status === "cancelling" ? (
          <span className="shrink-0 p-0.5 text-text-4" aria-label="取消中">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          </span>
        ) : (
          <IconAction label="取消这个任务" onClick={() => onCancel(t.task_id)} danger>
            <X className="h-3.5 w-3.5" />
          </IconAction>
        )}
      </div>
      <div className="mt-0.5 flex items-center gap-2 pl-[31px] text-[11px] text-text-4">
        <span className="truncate">{modelText}</span>
        <span className="ml-auto shrink-0">{STATUS_LABELS[t.status]}</span>
      </div>
      {showWarnings && (
        <ul className="mt-1 space-y-0.5 pl-[31px] text-[11px] leading-[1.45] text-warm">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      {t.status === "running" && <RunningBar />}
    </div>
  );
}

function HudRecordRow({
  record: r,
  onRetryDownload,
  retrying,
  dense,
}: {
  record: UsageRecord;
  onRetryDownload?: (id: number) => void;
  retrying?: boolean;
  /** B 变体：单行密排，不要第二行 */
  dense?: boolean;
}) {
  const [, openDetail] = useRecordParam();
  const failed = r.status === "failed";
  const pending = r.status === "pending";
  const cost = r.cost_amount > 0 ? money(r.currency, r.cost_amount, r.cost_amount < 0.1 ? 3 : 2) : "—";
  const modelText = `${providerLabel(r.provider)} · ${r.model}`;
  const error = failed ? (r.error_code ? ERROR_LABELS[r.error_code] : r.error_message) : null;
  const canRetry = failed && r.error_code === "download_failed" && onRetryDownload;
  const retry = canRetry ? (
    retrying ? (
      <span className="inline-flex shrink-0 items-center gap-1 text-[11px] text-text-4">
        <Loader2 className="h-3 w-3 animate-spin" />
        重试中
      </span>
    ) : (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onRetryDownload(r.id);
        }}
        className={"inline-flex shrink-0 items-center gap-1 rounded-[5px] border border-hairline px-1.5 py-px text-[11px] text-text-2 transition-colors hover:border-accent-soft hover:text-accent-2 " + FOCUS}
      >
        <RotateCcw className="h-3 w-3" />
        重试下载
      </button>
    )
  ) : null;

  if (dense) {
    return (
      <button
        type="button"
        onClick={() => openDetail(r.id)}
        className={"flex w-full items-center gap-2 rounded-[6px] px-2 py-1.5 text-left transition-colors hover:bg-bg-grad-a/70 " + FOCUS + (failed ? " bg-[oklch(0.30_0.10_25/0.12)]" : "")}
      >
        <MediaGlyph type={r.media_type} size={11} />
        <span className="min-w-0 shrink truncate text-[12px] text-text">{targetOf(r)}</span>
        <span className="min-w-0 flex-1 truncate text-[11px] text-text-4">{r.model}</span>
        {failed && !canRetry && <span className="shrink-0 text-[11px] text-danger-2">{(r.error_code && ERROR_SHORT[r.error_code]) || "失败"}</span>}
        {retry}
        <StatusPill status={r.status} compact />
        <span className="num w-[64px] shrink-0 whitespace-nowrap text-right text-[11px] text-text-4">{shortTime(r.started_at)}</span>
        <span className="num w-[58px] shrink-0 text-right text-[12px] text-text-2">{cost}</span>
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={() => openDetail(r.id)}
      className={"block w-full rounded-[8px] px-2.5 py-2 text-left transition-colors hover:bg-bg-grad-a/70 " + FOCUS + (failed ? " bg-[oklch(0.30_0.10_25/0.14)]" : "")}
    >
      <div className="flex items-center gap-2.5">
        <MediaGlyph type={r.media_type} size={12} />
        <span className="min-w-0 flex-1 truncate text-[12.5px] text-text">{targetOf(r)}</span>
        {retry}
        <StatusPill status={r.status} compact />
        <span className="num w-[52px] shrink-0 text-right text-[12px] text-text-2">{pending ? <Elapsed from={r.started_at} /> : cost}</span>
      </div>
      <div className="mt-0.5 flex items-center gap-2 pl-[31px] text-[11px] text-text-4">
        <span className="truncate">{modelText}</span>
        <span className="ml-auto shrink-0 num">{shortTime(r.started_at)}</span>
      </div>
      {error && <div className="mt-1 pl-[31px] text-[11px] leading-[1.45] text-danger-2">{error}</div>}
    </button>
  );
}

function ActiveList({ rows, data }: { rows: HudActive[]; data: ScenarioData }) {
  return (
    <>
      {rows.map((row) =>
        row.kind === "task" ? (
          <HudTaskRow key={row.task.task_id} task={row.task} onCancel={data.cancelTask} />
        ) : (
          <HudRecordRow key={row.record.id} record={row.record} />
        ),
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// 取消确认（语义同 TaskHud：单个取消要预览级联，全部取消只清排队中）
// ---------------------------------------------------------------------------

type CancelConfirm = { taskId: string } | { queued: number } | null;

function CancelConfirmBox({ confirm, onConfirm, onBack }: { confirm: NonNullable<CancelConfirm>; onConfirm: () => void; onBack: () => void }) {
  return (
    <div className="px-4 py-3" role="alertdialog" aria-label="确认取消" style={{ background: "oklch(0.16 0.010 265 / 0.5)", borderTop: "1px solid var(--color-hairline-soft)" }}>
      <p className="text-[12px] text-text-2">{"taskId" in confirm ? "取消这个任务？已产生的调用仍会计入参考费用。" : `取消排队中的 ${confirm.queued} 个任务？正在生成的不受影响。`}</p>
      <div className="mt-2.5 flex gap-2">
        <button
          type="button"
          onClick={onConfirm}
          className={"rounded px-2.5 py-1 text-[11px] font-medium " + FOCUS}
          style={{ color: "oklch(0.98 0 0)", background: "linear-gradient(135deg, oklch(0.55 0.20 25), oklch(0.45 0.18 25))" }}
        >
          确认取消
        </button>
        <button type="button" onClick={onBack} className={"rounded border border-hairline px-2.5 py-1 text-[11px] text-text-3 hover:text-text " + FOCUS}>
          返回
        </button>
      </div>
    </div>
  );
}

function useCancelFlow(data: ScenarioData) {
  const [confirm, setConfirm] = useState<CancelConfirm>(null);
  const askSingle = (taskId: string) => setConfirm({ taskId });
  const askAll = () => setConfirm({ queued: data.queued });
  const run = () => {
    if (!confirm) return;
    if ("taskId" in confirm) data.cancelTask(confirm.taskId);
    else data.cancelQueued();
    setConfirm(null);
  };
  const dataWithConfirm: ScenarioData = { ...data, cancelTask: askSingle };
  const box = confirm ? <CancelConfirmBox confirm={confirm} onConfirm={run} onBack={() => setConfirm(null)} /> : null;
  return { data: dataWithConfirm, askAll, box };
}

// ---------------------------------------------------------------------------
// 共享块：头部、KPI、空态、「查看全部记录」
// ---------------------------------------------------------------------------

function useGoToAll() {
  const [, navigate] = useLocation();
  return () => navigate(`~/app/settings?section=usage-prototype&variant=A&u_project=${encodeURIComponent(PROJECT)}`);
}

function HeaderBlock({ onClose, right }: { onClose: () => void; right?: ReactNode }) {
  return (
    <div className="relative flex items-center gap-2 px-4 py-3" style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}>
      <span
        aria-hidden
        className="grid h-7 w-7 place-items-center rounded-lg"
        style={{
          background: "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.05))",
          border: "1px solid var(--color-accent-soft)",
          color: "var(--color-accent-2)",
          boxShadow: "0 8px 18px -8px var(--color-accent-glow)",
        }}
      >
        <Activity className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0">
        <div className="display-serif text-[14px] font-semibold tracking-tight text-text">使用记录</div>
        <div className="num text-[10px] uppercase text-text-4" style={{ letterSpacing: "1.2px" }}>
          Usage · {PROJECT}
        </div>
      </div>
      <div className="flex-1" />
      {right}
      <ModalCloseButton onClick={onClose} />
    </div>
  );
}

function KpiCell({ kicker, value, sub, tone }: { kicker: string; value: ReactNode; sub?: ReactNode; tone?: "danger" }) {
  return (
    <div className="min-w-0">
      <Kicker tone="muted">{kicker}</Kicker>
      <div className={"num mt-0.5 truncate text-[15px] font-semibold tracking-tight " + (tone === "danger" ? "text-danger-2" : "text-text")}>{value}</div>
      {sub && <div className="num truncate text-[10.5px] text-text-4">{sub}</div>}
    </div>
  );
}

function KpiRow({ kpi }: { kpi: Kpi }) {
  const { main, others } = costParts(kpi);
  return (
    <div className="grid grid-cols-4 gap-3 px-4 py-3" style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}>
      <KpiCell kicker="Calls" value={kpi.calls} sub="本项目 · 全部" />
      <KpiCell kicker="Success" value={pct(kpi.success_rate)} sub={`${kpi.success} 次成功`} />
      <KpiCell kicker="Failed" value={kpi.failed} tone={kpi.failed > 0 ? "danger" : undefined} sub={kpi.cancelled > 0 ? `${kpi.cancelled} 次取消` : undefined} />
      <KpiCell kicker="Ref. cost" value={main ?? "—"} sub={others.length > 0 ? `+ ${others.join(" + ")}` : kpi.primary ? `只计 ${kpi.primary}` : undefined} />
    </div>
  );
}

function Empty({ title, hint, compact }: { title: string; hint?: string; compact?: boolean }) {
  return (
    <div className={"flex flex-col items-center justify-center text-center " + (compact ? "px-4 py-5" : "px-6 py-9")}>
      <div className="text-[12.5px] text-text-2">{title}</div>
      {hint && <div className="mt-1 max-w-[22rem] text-[11px] leading-[1.5] text-text-4">{hint}</div>}
    </div>
  );
}

const EMPTY_ALL = { title: "本项目还没有使用记录", hint: "生成图片、视频、配音或剧本后，每次调用都会记在这里。" };
const EMPTY_DONE = { title: "还没有已结束的调用", hint: "进行中的任务结束后会移到这里。" };

function SectionHead({ kicker, count, action }: { kicker: string; count: number; action?: ReactNode }) {
  return (
    <div className="flex items-center gap-2 px-2.5 pb-1 pt-2">
      <Kicker tone="muted">
        {kicker} · {count}
      </Kicker>
      <span className="flex-1" />
      {action}
    </div>
  );
}

function CancelAllButton({ queued, onClick }: { queued: number; onClick: () => void }) {
  if (queued === 0) return null;
  return (
    <button type="button" onClick={onClick} className={"rounded px-1.5 py-0.5 text-[10.5px] text-text-4 transition-colors hover:text-danger-2 " + FOCUS} aria-label={`取消排队中的 ${queued} 个任务`}>
      全部取消
    </button>
  );
}

// ---------------------------------------------------------------------------
// 变体 A：时间线 —— 26rem 单列，KPI 4 格，进行中与最近同一条流，底部通栏「查看全部记录」
// ---------------------------------------------------------------------------

const RECENT_N: Record<string, number> = { A: 8, B: 5, C: 10 };

function PopoverA({ data: raw, onClose }: { data: ScenarioData; onClose: () => void }) {
  const { data, askAll, box } = useCancelFlow(raw);
  const goAll = useGoToAll();
  const recent = data.recent.slice(0, RECENT_N.A);
  const nothing = data.active.length === 0 && data.recent.length === 0;
  return (
    <>
      <HeaderBlock onClose={onClose} />
      <KpiRow kpi={data.kpi} />
      <div className="max-h-[min(32rem,calc(100vh-14rem))] overflow-y-auto px-1.5 pb-1.5">
        {nothing ? (
          <Empty {...EMPTY_ALL} />
        ) : (
          <>
            {data.active.length > 0 && (
              <>
                <SectionHead kicker="In progress" count={data.active.length} action={<CancelAllButton queued={data.queued} onClick={askAll} />} />
                <ActiveList rows={data.active} data={data} />
              </>
            )}
            <SectionHead kicker="Recent" count={recent.length} />
            {recent.length === 0 ? (
              <Empty {...EMPTY_DONE} compact />
            ) : (
              recent.map((r) => <HudRecordRow key={r.id} record={r} onRetryDownload={data.retryDownload} retrying={data.retrying.has(r.id)} />)
            )}
          </>
        )}
      </div>
      {box}
      <div className="px-3 py-2.5" style={{ borderTop: "1px solid var(--color-hairline-soft)" }}>
        <button
          type="button"
          onClick={goAll}
          className={"flex w-full items-center justify-center gap-1.5 rounded-[7px] border border-hairline py-1.5 text-[12px] text-text-2 transition-colors hover:border-accent-soft hover:bg-bg-grad-a hover:text-accent-2 " + FOCUS}
        >
          查看全部记录
          <ArrowUpRight className="h-3.5 w-3.5" />
        </button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// 变体 B：摘要句 + 直播卡 —— 28rem，KPI 压成一句话，进行中单独成卡，最近一行一条，「查看全部记录」在头部
// ---------------------------------------------------------------------------

const Sep = () => <span className="text-text-4">·</span>;

function SummarySentence({ kpi }: { kpi: Kpi }) {
  const { main, others } = costParts(kpi);
  if (kpi.calls === 0) return <div className="px-4 py-2.5 text-[11.5px] text-text-4">本项目还没有已结束的调用</div>;
  return (
    <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 px-4 py-2.5 text-[12px] text-text-2" style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}>
      <span className="text-text-4">本项目 · 全部</span>
      <Sep />
      <span>
        <span className="num font-semibold text-text">{kpi.calls}</span> 次调用
      </span>
      <Sep />
      <span>
        <span className="num font-semibold text-text">{pct(kpi.success_rate)}</span> 成功
      </span>
      {kpi.failed > 0 && (
        <>
          <Sep />
          <span className="text-danger-2">
            <span className="num font-semibold">{kpi.failed}</span> 次失败
          </span>
        </>
      )}
      <span className="basis-full">
        参考费用 <span className="num font-semibold text-text">{main ?? "—"}</span>
        {others.length > 0 && <span className="num text-text-4"> + {others.join(" + ")}</span>}
      </span>
    </div>
  );
}

function PopoverB({ data: raw, onClose }: { data: ScenarioData; onClose: () => void }) {
  const { data, askAll, box } = useCancelFlow(raw);
  const goAll = useGoToAll();
  const recent = data.recent.slice(0, RECENT_N.B);
  const nothing = data.active.length === 0 && data.recent.length === 0;
  return (
    <>
      <HeaderBlock
        onClose={onClose}
        right={
          <button type="button" onClick={goAll} className={"mr-1 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11.5px] text-text-3 transition-colors hover:text-accent-2 " + FOCUS}>
            查看全部记录
            <ArrowUpRight className="h-3 w-3" />
          </button>
        }
      />
      <SummarySentence kpi={data.kpi} />
      {nothing ? (
        <Empty {...EMPTY_ALL} />
      ) : (
        <div className="max-h-[min(32rem,calc(100vh-14rem))] overflow-y-auto p-2.5">
          {data.active.length > 0 && (
            <div className="relative mb-2.5 overflow-hidden rounded-[10px] border border-accent-soft" style={{ background: "linear-gradient(180deg, oklch(0.76 0.09 295 / 0.07), transparent 60%)" }}>
              <span aria-hidden className="arc-glass-hairline" data-tone="accent" />
              <div className="flex items-center gap-2 px-2.5 pt-2.5 pb-1">
                <span aria-hidden className="animate-breathe h-[6px] w-[6px] rounded-full bg-accent" style={{ boxShadow: "0 0 0 3px var(--color-accent-soft)" }} />
                <span className="text-[12px] font-medium text-text">
                  进行中 <span className="num text-text-3">{data.active.length}</span>
                </span>
                <span className="flex-1" />
                <CancelAllButton queued={data.queued} onClick={askAll} />
              </div>
              <div className="px-1 pb-1">
                <ActiveList rows={data.active} data={data} />
              </div>
            </div>
          )}
          <div className="px-1 pb-1">
            <Kicker tone="muted">Recent</Kicker>
          </div>
          {recent.length === 0 ? (
            <Empty {...EMPTY_DONE} compact />
          ) : (
            recent.map((r) => <HudRecordRow key={r.id} record={r} dense onRetryDownload={data.retryDownload} retrying={data.retrying.has(r.id)} />)
          )}
        </div>
      )}
      {box}
    </>
  );
}

// ---------------------------------------------------------------------------
// 变体 C：宽版双栏 —— 40rem，左栏进行中（常驻）、右栏最近，「查看全部记录」在底部右侧
// ---------------------------------------------------------------------------

function PopoverC({ data: raw, onClose }: { data: ScenarioData; onClose: () => void }) {
  const { data, askAll, box } = useCancelFlow(raw);
  const goAll = useGoToAll();
  const recent = data.recent.slice(0, RECENT_N.C);
  return (
    <>
      <HeaderBlock onClose={onClose} />
      <KpiRow kpi={data.kpi} />
      <div className="grid grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
        <div className="max-h-[min(32rem,calc(100vh-14rem))] overflow-y-auto px-1.5 pb-1.5" style={{ borderRight: "1px solid var(--color-hairline-soft)" }}>
          <SectionHead kicker="In progress" count={data.active.length} action={<CancelAllButton queued={data.queued} onClick={askAll} />} />
          {data.active.length === 0 ? <Empty title="没有进行中的任务" hint="排队或生成时会出现在这里。" compact /> : <ActiveList rows={data.active} data={data} />}
        </div>
        <div className="max-h-[min(32rem,calc(100vh-14rem))] overflow-y-auto px-1.5 pb-1.5">
          <SectionHead kicker="Recent" count={recent.length} />
          {recent.length === 0 ? (
            <Empty {...(data.active.length === 0 ? EMPTY_ALL : EMPTY_DONE)} compact />
          ) : (
            recent.map((r) => <HudRecordRow key={r.id} record={r} onRetryDownload={data.retryDownload} retrying={data.retrying.has(r.id)} />)
          )}
        </div>
      </div>
      {box}
      <div className="flex items-center px-4 py-2" style={{ borderTop: "1px solid var(--color-hairline-soft)" }}>
        <span className="text-[10.5px] text-text-4">只显示本项目；进行中区不受时间范围影响</span>
        <span className="flex-1" />
        <button type="button" onClick={goAll} className={"inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[12px] text-text-2 transition-colors hover:text-accent-2 " + FOCUS}>
          查看全部记录
          <ArrowUpRight className="h-3.5 w-3.5" />
        </button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// 入口按钮：活动图标 + 本项目参考费用；进行中时呼吸 + 计数。三个变体各给一种多币种截断与计数落位
// ---------------------------------------------------------------------------

function EntryButton({
  variant,
  kpi,
  running,
  queued,
  open,
  onClick,
  controlsId,
}: {
  variant: string;
  kpi: Kpi;
  running: number;
  queued: number;
  open: boolean;
  onClick: () => void;
  controlsId: string;
}) {
  const { main, others } = costParts(kpi);
  const live = running + queued > 0;
  const full = [main, ...others].filter(Boolean).join(" + ");
  const title = `${main ? `本项目参考费用 ${full}` : "本项目还没有参考费用"}${live ? ` · ${running} 个进行中，${queued} 个排队` : ""}`;

  const icon = (
    <span className="relative grid h-4 w-4 place-items-center">
      {live && <span aria-hidden className="animate-breathe absolute inset-[-3px] rounded-full" style={{ background: "var(--color-accent-soft)" }} />}
      <Activity className="relative h-3.5 w-3.5" style={{ color: live || open ? "var(--color-accent-2)" : "var(--color-text-3)" }} />
    </span>
  );
  const badge = live && (
    <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold" style={{ background: "var(--color-accent)", color: "oklch(0.14 0 0)" }}>
      {running + queued}
    </span>
  );

  let cost: ReactNode;
  if (variant === "B") {
    // B：主币种 + 其他币种全列（≤2 种时），计数嵌在按钮内部而非角标
    cost = (
      <span className="num inline-flex items-baseline gap-1.5">
        <span className={main ? "text-text-2" : "text-text-4"}>{main ?? "¥0.00"}</span>
        {others.slice(0, 1).map((o) => (
          <span key={o} className="text-[10.5px] text-text-4">
            + {o}
          </span>
        ))}
        {others.length > 1 && <span className="text-[10.5px] text-text-4">+{others.length - 1}</span>}
      </span>
    );
  } else if (variant === "C") {
    // C：只显示主币种，其他币种只留一个「+」角标（同 KPI 格的约定）
    cost = (
      <span className="num inline-flex items-start">
        <span className={main ? "text-text-2" : "text-text-4"}>{main ?? "¥0.00"}</span>
        {others.length > 0 && (
          <span className="ml-px text-[9px] leading-none text-accent-2" aria-label={`另有 ${others.join("、")}`}>
            +
          </span>
        )}
      </span>
    );
  } else {
    // A：主币种 + 「+N」上标表示还有 N 种币种
    cost = (
      <span className="num inline-flex items-start">
        <span className={main ? "text-text-2" : "text-text-4"}>{main ?? "¥0.00"}</span>
        {others.length > 0 && <span className="ml-0.5 text-[9px] leading-none text-text-4">+{others.length}</span>}
      </span>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={open}
      aria-controls={controlsId}
      aria-label={title}
      title={title}
      className={"relative inline-flex h-[30px] items-center gap-2 rounded-md pl-2 pr-2.5 text-[11.5px] transition-colors " + FOCUS}
      style={{
        background: open ? "var(--color-accent-dim)" : "oklch(0.22 0.011 265 / 0.5)",
        border: `1px solid ${live ? "var(--color-accent-soft)" : "var(--color-hairline-soft)"}`,
      }}
    >
      {icon}
      {variant === "B" && live && (
        <span className="num rounded-[4px] px-1 text-[10.5px] font-bold" style={{ background: "var(--color-accent)", color: "oklch(0.14 0 0)" }}>
          {running + queued}
        </span>
      )}
      {cost}
      {variant !== "B" && badge}
    </button>
  );
}

// ---------------------------------------------------------------------------
// 宿主：挂在 GlobalHeader 右侧动作区，取代费用徽章 + 任务雷达
// ---------------------------------------------------------------------------

const WIDTH: Record<string, string> = { A: "w-[26rem]", B: "w-[28rem]", C: "w-[40rem]" };

export function UsageEntryPrototype({ scenario }: { scenario: string }) {
  const search = useSearch();
  const [location, navigate] = useLocation();
  const params = new URLSearchParams(search);
  const variant = params.get("variant") ?? "A";
  const scen: Scenario = (SCENARIOS.some((s) => s.key === scenario) ? scenario : "busy") as Scenario;
  const data = useScenarioData(scen);
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLDivElement>(null);

  const setScenario = useCallback(
    (key: Scenario) => {
      const p = new URLSearchParams(search);
      p.set("ue", key);
      navigate(`${location}?${p.toString()}`, { replace: true });
    },
    [location, navigate, search],
  );
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (el instanceof HTMLElement && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
      const hit = SCENARIOS.find((s) => s.hint === e.key);
      if (hit) setScenario(hit.key);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setScenario]);

  const Body = variant === "B" ? PopoverB : variant === "C" ? PopoverC : PopoverA;

  return (
    <>
      <div className="relative" ref={anchorRef}>
        <EntryButton variant={variant} kpi={data.kpi} running={data.running} queued={data.queued} open={open} onClick={() => setOpen((v) => !v)} controlsId="usage-entry-popover" />
        <GlassPopover open={open} onClose={() => setOpen(false)} anchorRef={anchorRef} sideOffset={6} width={WIDTH[variant] ?? WIDTH.A}>
          <div id="usage-entry-popover">
            <Body data={data} onClose={() => setOpen(false)} />
          </div>
        </GlassPopover>
      </div>
      <RecordDetailHost />
      {createPortal(
        <>
      <PrototypeSwitcher variants={VARIANTS} current={variant} />
          {import.meta.env.DEV && (
            <div className="fixed bottom-5 left-5 z-50 flex items-center gap-1 rounded-full border px-1.5 py-1 shadow-2xl shadow-black/60" style={{ background: "oklch(0.98 0 0)", borderColor: "oklch(0.85 0 0)", color: "oklch(0.2 0 0)" }}>
              {SCENARIOS.map((s) => (
                <button
                  key={s.key}
                  type="button"
                  onClick={() => setScenario(s.key)}
                  className="rounded-full px-2 py-0.5 font-mono text-[10.5px]"
                  style={{ background: s.key === scen ? "oklch(0.2 0 0)" : "transparent", color: s.key === scen ? "oklch(0.98 0 0)" : "inherit" }}
                >
                  {s.hint} · {s.name}
                </button>
              ))}
            </div>
          )}
    
        </>,
        document.body,
      )}
    </>
  );
}
