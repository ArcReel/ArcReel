// PROTOTYPE（#2829，一次性代码，勿合入 main）
// 三个变体，URL 带 ?variant=A|B|C 时替换顶栏中间、迁移横幅，并在项目概览页顶部挂项目层引导；
// ?pscenario= 选项目层场景（右下角）。集页面板固定为 #2828 选定的 A，?scenario= 选集层场景（左下角）。
//   A「进度胶囊」：顶栏只放集进度，项目层下一步在概览页的引导卡；迁移走横幅。
//   B「顶栏下一步」：顶栏放项目层下一步，集页上与本集重复时让位给面板；迁移占据顶栏中间。
//   C「项目面板」：概览页一块与集页同构的项目面板，顶栏放逐集分段进度条；迁移是面板第一行。
import { useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, Loader2 } from "lucide-react";
import { useLocation, useSearch } from "wouter";
import { create } from "zustand";
import { PrototypeSwitcher, usePrototypeVariant } from "@/components/shared/PrototypeSwitcher";
import type { Act } from "../panel-prototype/model";
import { ActButton, Alternatives, LogCtx, NoteLine } from "../panel-prototype/shared";
import {
  EP_COLOR,
  EP_LABEL,
  PROJECT_SCENARIOS,
  doneCount,
  retryAgentAct,
  useRetry,
  type EpState,
  type ProjectNext,
  type ProjectScenario,
} from "./model";

export const VARIANTS = [
  { key: "A", name: "进度胶囊 + 概览引导卡" },
  { key: "B", name: "顶栏下一步" },
  { key: "C", name: "项目面板 + 分段进度条" },
];

export function useProtoActive(): boolean {
  const search = useSearch();
  return new URLSearchParams(search).has("variant") || new URLSearchParams(window.location.search).has("variant");
}

function useVariant() {
  return usePrototypeVariant(VARIANTS.map((v) => v.key));
}

function usePScenario(): ProjectScenario {
  useSearch();
  const id = new URLSearchParams(window.location.search).get("pscenario");
  return PROJECT_SCENARIOS.find((s) => s.id === id) ?? PROJECT_SCENARIOS[0];
}

/** 当前所在集页的集号；不在集页时为 null。 */
function useCurrentEpisode(): number | null {
  useLocation();
  const m = /\/episodes\/(\d+)/.exec(window.location.pathname);
  return m ? Number(m[1]) : null;
}

function useMigrationActive(p: ProjectScenario) {
  const phase = useRetry((s) => s.phase);
  return Boolean(p.migration) && phase !== "succeeded";
}

// ---- 点击回显 --------------------------------------------------------------------------

const useLast = create<{ last: Act | null; set: (a: Act) => void }>((set) => ({
  last: null,
  set: (last) => set({ last }),
}));

function Log({ children }: { children: ReactNode }) {
  const set = useLast((s) => s.set);
  return <LogCtx.Provider value={set}>{children}</LogCtx.Provider>;
}

// ---- 控件 ------------------------------------------------------------------------------

const hair = { borderColor: "var(--color-hairline)" };

function RetryButton({ size = "md" }: { size?: "sm" | "md" }) {
  const { phase, retry } = useRetry();
  const pad = size === "sm" ? "px-2 py-0.5 text-[11.5px]" : "px-2.5 py-1 text-[12px]";
  const running = phase === "running";
  return (
    <button
      type="button"
      onClick={retry}
      disabled={running}
      className={`focus-ring inline-flex shrink-0 items-center gap-1.5 rounded-md font-medium ${pad} disabled:cursor-wait`}
      style={{ background: "var(--color-text)", color: "oklch(0.15 0 0)" }}
    >
      {running && <Loader2 className="h-3 w-3 animate-spin" />}
      {running ? "正在重试…" : phase === "failed" ? "再试一次" : "重试"}
    </button>
  );
}

/** 迁移失败的说明文字：首次失败与重试失败措辞不同。 */
function MigrationText({ p, compact }: { p: ProjectScenario; compact?: boolean }) {
  const { phase, attempts, lastError } = useRetry();
  const failedAgain = phase === "failed";
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
      <p className="m-0 text-[12px] font-semibold leading-[1.55]" style={{ color: "var(--color-text)" }}>
        {failedAgain ? `重试没有成功（已重试 ${attempts} 次）` : "项目数据升级没有完成，生成入口已关闭"}
      </p>
      {!compact && (
        <p className="m-0 text-[12px] leading-[1.55]" style={{ color: "var(--color-text-2)" }}>
          {failedAgain
            ? "问题出在项目文件本身，再试多半还是同样结果。可以交给 Agent 排查修复，或手动修好文件后再试。"
            : "已有的内容照常可看。重试会从失败的那一步接着升级。"}
        </p>
      )}
      <p className="m-0 break-words font-mono text-[11.5px] leading-[1.5]" style={{ color: "var(--color-text-3)" }}>
        {lastError ?? p.migration?.reason}
      </p>
    </div>
  );
}

function RetrySucceeded() {
  const reset = useRetry((s) => s.reset);
  return (
    <div className="flex items-center gap-2 border-b px-5 py-2 text-[12px]" style={{ borderColor: "var(--color-hairline)", background: "oklch(0.3 0.05 150 / 0.25)" }}>
      <CheckCircle2 className="h-4 w-4" style={{ color: "var(--color-good)" }} />
      <span style={{ color: "var(--color-text)" }}>数据升级已完成，生成入口已恢复。</span>
      <button type="button" className="ml-auto text-[11.5px] underline" style={{ color: "var(--color-text-3)" }} onClick={reset}>
        （原型）回到失败状态
      </button>
    </div>
  );
}

function NextBlock({ next, compact }: { next: ProjectNext; compact?: boolean }) {
  return (
    <div className="space-y-1.5">
      <div className="text-[12.5px]">
        <span className="font-medium" style={{ color: "var(--color-accent-2)" }}>下一步：{next.title}</span>
        <span style={{ color: "var(--color-text-3)" }}> — {next.detail}</span>
      </div>
      {next.instruction !== undefined && <PlanningInstruction value={next.instruction} compact={compact} />}
      <div className="flex flex-wrap items-center gap-2">
        {next.primary.map((a) => <ActButton key={a.label} act={a} />)}
        <Alternatives acts={next.alternatives} />
      </div>
    </div>
  );
}

function PlanningInstruction({ value, compact }: { value: string; compact?: boolean }) {
  const [v, setV] = useState(value);
  return (
    <label className="block max-w-[460px]">
      {!compact && (
        <span className="mb-0.5 block text-[11px]" style={{ color: "var(--color-text-3)" }}>
          附加要求（可选，保留上一次的输入，不保存到项目）
        </span>
      )}
      <input
        value={v}
        onChange={(e) => setV(e.target.value)}
        placeholder="例如：每集控制在 90 秒左右，在悬念处断开"
        className="focus-ring w-full rounded-md px-2 py-1 text-[12px]"
        style={{ background: "var(--color-surface-2)", border: "1px solid var(--color-hairline)", color: "var(--color-text)" }}
      />
    </label>
  );
}

function progressText(p: ProjectScenario) {
  if (p.episodes.length === 0) return "尚未建集";
  if (p.content === "ad") return p.episodes[0].state === "done" ? "短片已完成" : "短片未完成";
  return `已完成 ${doneCount(p)} / ${p.episodes.length} 集`;
}

function staleCount(p: ProjectScenario) {
  return p.episodes.filter((e) => e.state === "stale").length;
}

function EpisodeList({ p, current }: { p: ProjectScenario; current: number | null }) {
  const log = useLast((s) => s.set);
  return (
    <ol className="m-0 list-none space-y-0.5 p-0">
      {p.episodes.map((e) => (
        <li key={e.id}>
          <button
            type="button"
            onClick={() => log({ label: `第 ${e.id} 集`, kind: "nav", effect: `跳到第 ${e.id} 集` })}
            className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-[12px] hover:bg-[oklch(1_0_0_/_0.05)]"
            style={current === e.id ? { background: "var(--color-accent-dim)" } : undefined}
          >
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: EP_COLOR[e.state] }} />
            <span className="w-12 shrink-0 tabular-nums" style={{ color: "var(--color-text-3)" }}>第 {e.id} 集</span>
            <span className="min-w-0 flex-1 truncate" style={{ color: "var(--color-text)" }}>{e.title}</span>
            <span className="shrink-0 text-[11px]" style={{ color: e.state === "done" ? "var(--color-text-4)" : EP_COLOR[e.state] }}>
              {e.state === "done" ? "已完成" : e.next ? `下一步 · ${e.next}` : EP_LABEL[e.state]}
            </span>
          </button>
        </li>
      ))}
    </ol>
  );
}

function Popover({ children, width = 360 }: { children: ReactNode; width?: number }) {
  return (
    <div
      className="absolute left-1/2 top-[calc(100%+8px)] z-50 -translate-x-1/2 rounded-lg border p-2.5"
      style={{ ...hair, width, background: "oklch(0.2 0.011 265)", boxShadow: "0 16px 40px -12px oklch(0 0 0 / 0.7)" }}
    >
      {children}
    </div>
  );
}

// ---- 顶栏中间 ---------------------------------------------------------------------------

export function ProtoHeaderCenter() {
  const v = useVariant();
  return (
    <Log>
      {v === "A" && <HeaderA />}
      {v === "B" && <HeaderB />}
      {v === "C" && <HeaderC />}
    </Log>
  );
}

// 三个顶栏都沿用原阶段条的胶囊语言：内凹轨道 + 凸起亮片 + 圆形数字徽标。

const TRACK = {
  background: "oklch(0.17 0.010 265 / 0.6)",
  border: "1px solid var(--color-hairline)",
  boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.25)",
};
const TRACK_WARM = {
  background: "var(--color-warm-soft)",
  border: "1px solid var(--color-warm-ring)",
  boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.25)",
};
const RAISED = {
  color: "var(--color-text)",
  background: "linear-gradient(180deg, oklch(0.30 0.012 265), oklch(0.26 0.012 265))",
  boxShadow: "0 0 0 1px var(--color-hairline-strong), 0 1px 2px oklch(0 0 0 / 0.3)",
};

function Track({ children, warm }: { children: ReactNode; warm?: boolean }) {
  return (
    <div className="inline-flex items-center gap-px rounded-full p-[3px]" style={warm ? TRACK_WARM : TRACK}>
      {children}
    </div>
  );
}

function Chip({ children, raised, onClick, title }: { children: ReactNode; raised?: boolean; onClick?: () => void; title?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="focus-ring inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-3 py-1 text-xs font-medium transition-colors"
      style={raised ? RAISED : { color: "var(--color-text-3)", background: "transparent" }}
    >
      {children}
    </button>
  );
}

function Badge({ children, on }: { children: ReactNode; on?: boolean }) {
  return (
    <span
      className="num inline-grid h-[15px] min-w-[15px] place-items-center rounded-full px-[3px] text-[10px] font-bold"
      style={
        on
          ? { background: "var(--color-accent)", color: "oklch(0.12 0 0)", boxShadow: "0 0 8px -1px var(--color-accent-glow)" }
          : { background: "oklch(0.32 0.012 265)", color: "var(--color-text-3)" }
      }
    >
      {children}
    </span>
  );
}

/** 15px 进度环，与数字徽标同尺寸。 */
function Ring({ done, total }: { done: number; total: number }) {
  const r = 6;
  const c = 2 * Math.PI * r;
  const f = total ? done / total : 0;
  return (
    <svg width="15" height="15" viewBox="0 0 15 15" aria-hidden>
      <circle cx="7.5" cy="7.5" r={r} fill="none" stroke="oklch(0.32 0.012 265)" strokeWidth="2.2" />
      <circle
        cx="7.5" cy="7.5" r={r} fill="none" stroke="var(--color-accent)" strokeWidth="2.2" strokeLinecap="round"
        strokeDasharray={`${c * f} ${c}`} transform="rotate(-90 7.5 7.5)"
        style={{ filter: "drop-shadow(0 0 3px var(--color-accent-glow))" }}
      />
    </svg>
  );
}

function WarmDot({ n }: { n: number }) {
  return (
    <span className="inline-flex items-center gap-1 text-[11px]" style={{ color: "var(--color-warm)" }}>
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--color-warm)" }} />
      <span className="num">{n}</span>
    </span>
  );
}

function HeaderA() {
  const p = usePScenario();
  const current = useCurrentEpisode();
  const [open, setOpen] = useState(false);
  const migration = useMigrationActive(p);
  const stale = staleCount(p);
  const has = p.episodes.length > 0;
  return (
    <div className="relative">
      <Track warm={migration}>
        <Chip raised onClick={() => has && setOpen((o) => !o)}>
          {migration ? <AlertTriangle className="h-3.5 w-3.5" style={{ color: "var(--color-warm)" }} /> : <Ring done={doneCount(p)} total={p.episodes.length} />}
          <span>{progressText(p)}</span>
          {has && <ChevronDown className="h-3 w-3" style={{ color: "var(--color-text-3)" }} />}
        </Chip>
        {p.planningProgress != null && (
          <Chip><Badge on>{p.planningProgress}</Badge>分集规划中</Chip>
        )}
        {stale > 0 && (
          <Chip title={`${stale} 集有产物需要更新`} onClick={() => setOpen((o) => !o)}>
            <WarmDot n={stale} />需要更新
          </Chip>
        )}
      </Track>
      {open && has && (
        <Popover>
          <EpisodeList p={p} current={current} />
          <div className="mt-2 border-t pt-2 text-[11.5px]" style={{ ...hair, color: "var(--color-text-3)" }}>
            项目层的下一步在
            <button type="button" className="mx-1 underline" style={{ color: "var(--color-text-2)" }}>项目概览</button>
          </div>
        </Popover>
      )}
    </div>
  );
}

function HeaderB() {
  const p = usePScenario();
  const current = useCurrentEpisode();
  const migration = useMigrationActive(p);
  const [open, setOpen] = useState<"eps" | "next" | "mig" | null>(null);
  const { phase, attempts } = useRetry();
  const toggle = (k: "eps" | "next" | "mig") => setOpen((o) => (o === k ? null : k));

  if (migration) {
    return (
      <div className="relative">
        <Track warm>
          <Chip onClick={() => toggle("mig")}>
            <AlertTriangle className="h-3.5 w-3.5" style={{ color: "var(--color-warm)" }} />
            <span style={{ color: "var(--color-text)" }}>
              {phase === "failed" ? `重试没有成功 · ${attempts} 次` : "数据升级没有完成"}
            </span>
            <ChevronDown className="h-3 w-3" />
          </Chip>
          <RetryButton size="sm" />
        </Track>
        {(open === "mig" || phase === "failed") && (
          <Popover width={440}>
            <div className="space-y-2">
              <MigrationText p={p} />
              <ActButton act={retryAgentAct} size="sm" />
            </div>
          </Popover>
        )}
      </div>
    );
  }

  const next = p.next;
  // 集页上：项目层下一步指向本集时让位给下方面板，只留进度。
  const yieldToPanel = current != null && next?.episode === current;
  return (
    <div className="relative">
      <Track>
        <Chip onClick={() => p.episodes.length && toggle("eps")}>
          <Ring done={doneCount(p)} total={p.episodes.length} />
          <span className="num">{p.episodes.length ? `${doneCount(p)}/${p.episodes.length}` : "尚未建集"}</span>
        </Chip>
        {next && !yieldToPanel && (
          <Chip raised onClick={() => toggle("next")} title={next.detail}>
            <Badge on>→</Badge>
            <span>{next.title}</span>
            <ChevronDown className="h-3 w-3" style={{ color: "var(--color-text-3)" }} />
          </Chip>
        )}
        {yieldToPanel && <Chip><Badge>{current}</Badge>当前集</Chip>}
      </Track>
      {open === "eps" && (
        <Popover><EpisodeList p={p} current={current} /></Popover>
      )}
      {open === "next" && next && (
        <Popover width={460}><NextBlock next={next} compact /></Popover>
      )}
    </div>
  );
}

const SEG: Record<EpState, React.CSSProperties> = {
  done: { background: "oklch(0.76 0.09 295 / 0.55)" },
  progress: { background: "var(--color-accent)", boxShadow: "0 0 8px -1px var(--color-accent-glow)" },
  todo: { background: "oklch(0.30 0.012 265)" },
  stale: { background: "var(--color-warm)" },
  repair: { background: "var(--color-danger)" },
};

function HeaderC() {
  const p = usePScenario();
  const current = useCurrentEpisode();
  const migration = useMigrationActive(p);
  const log = useLast((s) => s.set);
  const [hover, setHover] = useState<number | null>(null);
  const hovered = p.episodes.find((e) => e.id === hover);
  const n = p.episodes.length;
  const segW = Math.max(6, Math.min(18, 220 / Math.max(n, 1)));
  return (
    <Track warm={migration}>
      <Chip raised>
        {migration ? <AlertTriangle className="h-3.5 w-3.5" style={{ color: "var(--color-warm)" }} /> : <Ring done={doneCount(p)} total={n} />}
        <span className="num">{n ? (p.content === "ad" ? progressText(p) : `${doneCount(p)}/${n}`) : "尚未建集"}</span>
      </Chip>
      {n > 0 && p.content !== "ad" && (
        <div className="flex items-center gap-[3px] px-2.5" onMouseLeave={() => setHover(null)}>
          {p.episodes.map((e) => (
            <button
              key={e.id}
              type="button"
              onMouseEnter={() => setHover(e.id)}
              onClick={() => log({ label: `第 ${e.id} 集`, kind: "nav", effect: `跳到第 ${e.id} 集` })}
              aria-label={`第 ${e.id} 集 · ${EP_LABEL[e.state]}`}
              className="focus-ring h-[6px] rounded-full transition-transform hover:scale-y-150"
              style={{
                width: segW,
                ...SEG[e.state],
                outline: current === e.id ? "1.5px solid var(--color-text)" : undefined,
                outlineOffset: 2,
              }}
            />
          ))}
        </div>
      )}
      {(hovered || p.planningProgress != null) && (
        <span className="whitespace-nowrap pr-3 text-[11px]" style={{ color: "var(--color-text-3)" }}>
          {hovered
            ? `E${hovered.id} ${hovered.title} · ${hovered.next ? hovered.next : EP_LABEL[hovered.state]}`
            : `分集规划中 ${p.planningProgress}%`}
        </span>
      )}
    </Track>
  );
}


// ---- 迁移横幅（顶栏下方） ---------------------------------------------------------------

export function ProtoMigrationBanner() {
  const v = useVariant();
  const p = usePScenario();
  const phase = useRetry((s) => s.phase);
  const onOverview = useIsOverview();
  if (!p.migration) return null;
  if (phase === "succeeded") return <RetrySucceeded />;
  return (
    <Log>
      {v === "A" && (
        <div role="alert" className="flex shrink-0 items-start gap-2.5 border-b px-5 py-2.5" style={{ borderColor: "var(--color-warm-ring)", background: "var(--color-warm-soft)" }}>
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" style={{ color: "var(--color-warm)" }} />
          <MigrationText p={p} />
          <div className="flex shrink-0 flex-col items-end gap-1.5">
            <RetryButton />
            <ActButton act={{ ...retryAgentAct, kind: "nav" }} size="sm" />
          </div>
        </div>
      )}
      {/* B：顶栏中间已承担，不另设横幅。C：概览页由项目面板承担，其他页面只留一条去处理的细条。 */}
      {v === "C" && !onOverview && (
        <div className="flex shrink-0 items-center gap-2 border-b px-5 py-1.5 text-[12px]" style={{ borderColor: "var(--color-warm-ring)", background: "var(--color-warm-soft)" }}>
          <AlertTriangle className="h-3.5 w-3.5" style={{ color: "var(--color-warm)" }} />
          <span style={{ color: "var(--color-text)" }}>{phase === "failed" ? "数据升级重试没有成功" : "项目数据升级没有完成，生成入口已关闭"}</span>
          <ActButton act={{ label: "去项目概览处理", kind: "nav", effect: "跳到项目概览（项目面板第一行）" }} size="sm" />
        </div>
      )}
    </Log>
  );
}

function useIsOverview() {
  useLocation();
  return /\/projects\/[^/]+\/?$/.test(window.location.pathname);
}

// ---- 概览页顶部 ------------------------------------------------------------------------

export function ProtoOverviewGuide() {
  const v = useVariant();
  return (
    <Log>
      {v === "A" && <OverviewA />}
      {v === "C" && <OverviewC />}
      {v === "B" && (
        <div className="mx-auto max-w-5xl px-6 pt-4 text-[11.5px]" style={{ color: "var(--color-text-4)" }}>
          （变体 B：项目层下一步只在顶栏，概览页不另放。）
        </div>
      )}
    </Log>
  );
}

function OverviewA() {
  const p = usePScenario();
  const migration = useMigrationActive(p);
  return (
    <div className="mx-auto max-w-5xl px-6 pt-5">
      <section className="rounded-xl border p-4" style={{ ...hair, background: "var(--color-accent-dim)" }}>
        <div className="mb-2 flex items-center gap-2 text-[11px]" style={{ color: "var(--color-text-3)" }}>
          <span className="font-mono uppercase tracking-wider">Next</span>
          <span>· {progressText(p)}</span>
          {p.source && <span>· {p.source}</span>}
        </div>
        {migration ? (
          <div className="text-[12.5px]" style={{ color: "var(--color-text-2)" }}>先处理顶部的数据升级问题，完成后这里显示下一步。</div>
        ) : p.next ? (
          <NextBlock next={p.next} />
        ) : null}
        {p.reminders?.map((r) => (
          <div key={r.text} className="mt-2"><NoteLine note={{ tone: "warn", text: r.text, act: r.act }} /></div>
        ))}
      </section>
    </div>
  );
}

interface PRow {
  key: string;
  title: string;
  status: string;
  tone: "done" | "todo" | "warn" | "danger" | "running";
  notes?: { text: string; act?: Act }[];
}

function projectRows(p: ProjectScenario, migration: boolean): PRow[] {
  const rows: PRow[] = [];
  if (migration) rows.push({ key: "migration", title: "数据升级", status: "没有完成，生成入口已关闭", tone: "danger" });
  if (p.content === "ad") {
    rows.push({ key: "episodes", title: "短片", status: p.episodes[0]?.next ? `下一步 · ${p.episodes[0].next}` : "已完成", tone: "todo" });
    return rows;
  }
  rows.push({ key: "source", title: "整本源文", status: p.source ?? "没有（可逐集上传或手写）", tone: p.source ? "done" : "todo" });
  rows.push({
    key: "planning",
    title: "分集",
    status: p.episodes.length === 0 ? (p.planning ?? "还没有集") : `${p.episodes.length} 集${p.planning ? ` · ${p.planning}` : ""}`,
    tone: p.planningProgress != null ? "running" : p.episodes.length ? "done" : "todo",
  });
  const stale = staleCount(p);
  rows.push({
    key: "episodes",
    title: "各集制作",
    status: p.episodes.length === 0 ? "还没有集" : `已完成 ${doneCount(p)} / ${p.episodes.length} 集`,
    tone: doneCount(p) === p.episodes.length && p.episodes.length > 0 ? "done" : "todo",
    notes: stale > 0 ? p.reminders : undefined,
  });
  rows.push({ key: "edit", title: "剪辑", status: "占位（形态归「导出与剪辑时间线」）", tone: "todo" });
  return rows;
}

function nextRowKey(p: ProjectScenario, migration: boolean): string | null {
  if (migration) return "migration";
  const n = p.next;
  if (!n) return null;
  if (n.episode != null) return "episodes";
  if (/上传/.test(n.title)) return "source";
  if (/规划/.test(n.title)) return "planning";
  if (/剪辑/.test(n.title)) return "edit";
  return "episodes";
}

const ROW_COLOR = { done: "var(--color-accent-2)", todo: "var(--color-text-4)", warn: "var(--color-warm)", danger: "var(--color-warm)", running: "var(--color-accent-2)" };

function OverviewC() {
  const p = usePScenario();
  const migration = useMigrationActive(p);
  const [openState, setOpen] = useState(false);
  const open = openState || migration;
  const rows = projectRows(p, migration);
  const key = nextRowKey(p, migration);
  const collapsedPrimary = migration ? null : p.next?.primary;
  return (
    <div className="mx-auto max-w-5xl px-6 pt-5">
      <section className="rounded-xl border px-4 py-2" style={hair}>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <button type="button" onClick={() => setOpen((v) => !v)} className="focus-ring flex items-center gap-1.5 rounded text-[12.5px] font-medium" style={{ color: "var(--color-text)" }}>
            <ChevronDown className="h-3.5 w-3.5" style={{ transform: open ? "none" : "rotate(-90deg)" }} />
            项目进度
          </button>
          <span className="min-w-0 flex-1 truncate text-[12px]" style={{ color: "var(--color-text-3)" }}>
            {progressText(p)}{p.source ? ` · ${p.source}` : ""}
          </span>
          {!open && collapsedPrimary && (
            <span className="flex items-center gap-2">
              <span className="text-[11.5px]" style={{ color: "var(--color-text-3)" }}>下一步</span>
              {collapsedPrimary.map((a) => <ActButton key={a.label} act={a} size="sm" />)}
            </span>
          )}
        </div>
        {open && (
          <ol className="m-0 mt-2 list-none p-0">
            {rows.map((r) => {
              const isNext = r.key === key;
              return (
                <li key={r.key} className="flex gap-2.5 rounded-md py-1 pl-1.5 pr-2" style={isNext ? { background: r.key === "migration" ? "var(--color-warm-soft)" : "var(--color-accent-dim)", boxShadow: `inset 2px 0 0 ${r.key === "migration" ? "var(--color-warm)" : "var(--color-accent-2)"}` } : undefined}>
                  <span className="mt-[6px] inline-block h-2 w-2 shrink-0 rounded-full" style={r.tone === "todo" ? { border: `1px dashed ${ROW_COLOR.todo}` } : { background: ROW_COLOR[r.tone] }} />
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <span className="w-20 shrink-0 text-[12.5px] font-medium" style={{ color: "var(--color-text)" }}>{r.title}</span>
                      <span className="text-[12px]" style={{ color: r.tone === "danger" ? "var(--color-warm)" : "var(--color-text-2)" }}>{r.status}</span>
                    </div>
                    {r.key === "episodes" && p.episodes.length > 0 && p.content !== "ad" && (
                      <div className="pl-[88px]">
                        <details>
                          <summary className="cursor-pointer text-[11.5px]" style={{ color: "var(--color-text-3)" }}>逐集</summary>
                          <div className="max-w-[460px]"><EpisodeList p={p} current={null} /></div>
                        </details>
                      </div>
                    )}
                    {r.notes?.map((n) => <div key={n.text} className="pl-[88px]"><NoteLine note={{ tone: "warn", text: n.text, act: n.act }} /></div>)}
                    {isNext && (
                      <div className="pl-[88px] pt-1">
                        {r.key === "migration" ? (
                          <div className="space-y-1.5">
                            <MigrationText p={p} />
                            <div className="flex flex-wrap items-center gap-2">
                              <RetryButton />
                              <Alternatives acts={[{ ...retryAgentAct, kind: "nav" }]} />
                            </div>
                          </div>
                        ) : p.next ? (
                          <NextBlock next={p.next} compact />
                        ) : null}
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </section>
    </div>
  );
}

// ---- 原型控制台（右下角） ---------------------------------------------------------------

export function ProtoControls() {
  const [location, setLocation] = useLocation();
  const search = useSearch();
  const p = usePScenario();
  const last = useLast((s) => s.last);
  const { willFail, setWillFail, reset, phase } = useRetry();
  if (import.meta.env.PROD) return null;
  const choose = (id: string) => {
    const params = new URLSearchParams(search);
    params.set("pscenario", id);
    reset();
    setLocation(`${location}?${params.toString()}`, { replace: true });
  };
  return (
    <>
      <PrototypeSwitcher variants={VARIANTS} />
      <div
        className="fixed bottom-4 right-4 z-[100] w-[300px] space-y-1.5 rounded-xl p-2.5 text-[11.5px]"
        style={{ background: "oklch(0.95 0.12 95)", color: "oklch(0.2 0 0)", boxShadow: "0 8px 28px -6px oklch(0 0 0 / 0.6)" }}
      >
        <div className="font-semibold">PROTOTYPE #2829 · 项目层场景</div>
        <select value={p.id} onChange={(e) => choose(e.target.value)} className="w-full rounded border border-black/20 bg-white/70 px-1.5 py-1">
          {PROJECT_SCENARIOS.map((s) => (
            <option key={s.id} value={s.id}>{s.label}</option>
          ))}
        </select>
        {p.migration && (
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={willFail} onChange={(e) => setWillFail(e.target.checked)} />
            下次重试会失败（当前：{phase}）
          </label>
        )}
        <div className="opacity-70">顶栏、横幅、概览页顶部是原型；集页面板是 #2828 选定的 A（左下角切集层场景）。</div>
        {last && (
          <div className="rounded bg-black/10 px-1.5 py-1">
            点了「{last.label}」{last.disabled ? `（不可点：${last.disabled}）` : ""}
            {last.effect && <div className="opacity-80">→ {last.effect}</div>}
          </div>
        )}
      </div>
    </>
  );
}
