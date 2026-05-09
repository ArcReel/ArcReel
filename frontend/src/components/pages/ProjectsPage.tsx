import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { errMsg, voidCall, voidPromise } from "@/utils/async";
import { Link, useLocation } from "wouter";
import {
  AlertTriangle,
  Library,
  Loader2,
  MoreHorizontal,
  Plus,
  Search,
  Settings,
  Trash2,
  Upload,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { ArchiveDiagnosticsDialog } from "@/components/shared/ArchiveDiagnosticsDialog";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { Typewriter, type TypewriterSegment } from "@/components/ui/Typewriter";
import { CreateProjectModal } from "./CreateProjectModal";
import { OpenClawModal } from "./OpenClawModal";
import { rememberAssetLibraryReturnTo } from "./AssetLibraryPage";
import {
  PHASE_ORDER,
  type Phase,
  type ImportConflictPolicy,
  type ImportFailureDiagnostics,
  type ProjectStatus,
  type ProjectSummary,
} from "@/types";

// 项目大厅 · Darkroom
// 设计：导演的暗房（Claude Design 交付包 ArcReel Projects B Darkroom.html）
// 数据：仅消费 ProjectSummary 真实字段；hue 由 project.name 哈希派生

type PhaseFilter = Phase | "all";
type GreetingKey =
  | "lobby_hero_greeting_morning"
  | "lobby_hero_greeting_afternoon"
  | "lobby_hero_greeting_evening"
  | "lobby_hero_greeting_late";

interface PhaseTone {
  dot: string;
  text: string;
  glow: string;
}

const PHASE_TONE: Record<Phase, PhaseTone> = {
  setup: {
    dot: "oklch(0.64 0.020 265)",
    text: "oklch(0.78 0.010 265)",
    glow: "transparent",
  },
  worldbuilding: {
    dot: "oklch(0.78 0.10 220)",
    text: "oklch(0.86 0.06 220)",
    glow: "oklch(0.78 0.10 220 / 0.35)",
  },
  scripting: {
    dot: "oklch(0.80 0.12 75)",
    text: "oklch(0.90 0.08 75)",
    glow: "oklch(0.80 0.12 75 / 0.35)",
  },
  production: {
    dot: "oklch(0.76 0.09 295)",
    text: "oklch(0.88 0.05 295)",
    glow: "oklch(0.76 0.09 295 / 0.40)",
  },
  completed: {
    dot: "oklch(0.78 0.10 155)",
    text: "oklch(0.86 0.06 155)",
    glow: "oklch(0.78 0.10 155 / 0.35)",
  },
};

const POSTER_FX_STYLE: CSSProperties = {
  background:
    "linear-gradient(115deg, oklch(1 0 0 / 0.18) 0%, transparent 30%), linear-gradient(295deg, oklch(0 0 0 / 0.55) 0%, transparent 45%)",
};

const POSTER_GRID_STYLE: CSSProperties = {
  backgroundImage:
    "linear-gradient(oklch(1 0 0) 1px, transparent 1px), linear-gradient(90deg, oklch(1 0 0) 1px, transparent 1px)",
  backgroundSize: "40px 40px",
  maskImage: "radial-gradient(70% 70% at 50% 50%, black, transparent)",
  WebkitMaskImage: "radial-gradient(70% 70% at 50% 50%, black, transparent)",
};

const POSTER_SPROCKET_STYLE: CSSProperties = {
  background:
    "repeating-linear-gradient(0deg, oklch(0 0 0 / 0.6) 0 6px, transparent 6px 12px)",
};

const ACCENT_BUTTON_STYLE: CSSProperties = {
  color: "oklch(0.14 0 0)",
  background:
    "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
  boxShadow:
    "inset 0 1px 0 oklch(1 0 0 / 0.3), 0 0 0 1px oklch(0.55 0.10 295 / 0.4), 0 4px 14px -6px var(--color-accent)",
};

function hashHue(name: string, salt: number): number {
  let hash = salt;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return hash % 360;
}

function asProjectStatus(s: ProjectSummary["status"]): ProjectStatus | null {
  return s && "current_phase" in s ? (s as ProjectStatus) : null;
}

function projectActivityScore(p: ProjectSummary): number {
  const status = asProjectStatus(p.status);
  if (!status) return -1;
  if (status.current_phase === "production" && status.phase_progress < 1) {
    return 100 + status.phase_progress * 10;
  }
  if (status.current_phase === "completed") return -10;
  return PHASE_ORDER.indexOf(status.current_phase) * 10 + status.phase_progress;
}

function pickFeaturedProject(projects: ProjectSummary[]): ProjectSummary | null {
  let best: ProjectSummary | null = null;
  let bestScore = -Infinity;
  for (const p of projects) {
    const score = projectActivityScore(p);
    if (score > bestScore) {
      best = p;
      bestScore = score;
    }
  }
  return bestScore > 0 ? best : null;
}

function styleLabelOf(p: ProjectSummary, t: TFunction): string {
  if (p.style_template_id) return t(`templates:name.${p.style_template_id}`);
  if (p.style_image) return t("dashboard:style_custom");
  return t("dashboard:style_not_set");
}

function getGreetingKey(d = new Date()): GreetingKey {
  const h = d.getHours();
  if (h >= 5 && h < 11) return "lobby_hero_greeting_morning";
  if (h >= 11 && h < 14) return "lobby_hero_greeting_afternoon";
  if (h >= 14 && h < 22) return "lobby_hero_greeting_evening";
  return "lobby_hero_greeting_late";
}

// -- Poster -------------------------------------------------------------------

interface PosterProps {
  project: ProjectSummary;
  styleLabel: string;
  large?: boolean;
}

function Poster({ project, styleLabel, large = false }: PosterProps) {
  const hue1 = useMemo(() => hashHue(project.name, 17), [project.name]);
  const aspect = large ? "2.39 / 1" : "2 / 1";
  const radius = large ? 8 : 6;
  return (
    <div
      className="relative overflow-hidden"
      style={{
        width: "100%",
        aspectRatio: aspect,
        borderRadius: radius,
        background: `radial-gradient(120% 80% at 30% 30%, oklch(0.55 0.15 ${hue1}) 0%, oklch(0.28 0.08 ${(hue1 + 10) % 360}) 45%, oklch(0.14 0.02 265) 100%)`,
        boxShadow: "inset 0 0 0 1px oklch(1 0 0 / 0.06)",
      }}
    >
      {project.thumbnail ? (
        <img
          src={project.thumbnail}
          alt=""
          loading="lazy"
          decoding="async"
          className="absolute inset-0 h-full w-full object-cover opacity-90"
        />
      ) : null}
      <div aria-hidden className="pointer-events-none absolute inset-0" style={POSTER_FX_STYLE} />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-10"
        style={POSTER_GRID_STYLE}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-y-0 left-0 w-2.5 opacity-50"
        style={POSTER_SPROCKET_STYLE}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-y-0 right-0 w-2.5 opacity-50"
        style={POSTER_SPROCKET_STYLE}
      />
      <div
        className="absolute left-[18px] top-[14px] font-mono font-bold uppercase tabular-nums"
        style={{ color: "oklch(0.95 0 0 / 0.78)", fontSize: 9, letterSpacing: "0.14em" }}
      >
        {styleLabel}
      </div>
      <div className="absolute right-[18px] bottom-[14px] left-[18px]">
        <div
          className="font-editorial"
          style={{
            fontWeight: 400,
            fontSize: large ? 54 : 30,
            lineHeight: 0.95,
            color: "oklch(0.99 0.005 0)",
            letterSpacing: "-0.02em",
            textShadow: "0 2px 28px oklch(0 0 0 / 0.5)",
            wordBreak: "break-word",
            overflowWrap: "anywhere",
          }}
        >
          {project.title || project.name}
        </div>
      </div>
    </div>
  );
}

// -- PhasePill / EpisodeStrip -------------------------------------------------

function PhasePill({ phase, label }: { phase: Phase | null; label: string }) {
  const tone = phase ? PHASE_TONE[phase] : PHASE_TONE.setup;
  const isProduction = phase === "production";
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border border-hairline-soft bg-bg-grad-a/60 px-2 py-[2px] font-mono text-[10px] font-semibold uppercase tracking-[0.06em]"
      style={{ color: tone.text }}
    >
      <span
        aria-hidden
        className={isProduction ? "motion-safe:animate-pulse" : undefined}
        style={{
          width: 5,
          height: 5,
          borderRadius: 3,
          background: tone.dot,
          boxShadow: `0 0 6px ${tone.glow}`,
        }}
      />
      {label}
    </span>
  );
}

function episodeDotColor(
  i: number,
  summary: ProjectStatus["episodes_summary"],
): { bg: string; glow?: string } {
  const inProductionEnd = summary.completed + summary.in_production;
  const scriptedEnd = inProductionEnd + summary.scripted;
  if (i < summary.completed) return { bg: "var(--color-good)" };
  if (i < inProductionEnd) {
    return { bg: "var(--color-accent)", glow: "0 0 6px var(--color-accent-glow)" };
  }
  if (i < scriptedEnd) return { bg: "oklch(0.55 0.010 265)" };
  return { bg: "oklch(0.22 0.011 265)" };
}

function EpisodeStrip({ summary }: { summary: ProjectStatus["episodes_summary"] }) {
  if (summary.total === 0) return null;
  return (
    <div className="flex gap-[3px]">
      {Array.from({ length: summary.total }).map((_, i) => {
        const c = episodeDotColor(i, summary);
        return (
          <span
            key={i}
            className="h-[3px] flex-1 rounded-[1.5px]"
            style={{ background: c.bg, boxShadow: c.glow }}
          />
        );
      })}
    </div>
  );
}

// -- 渐变进度条 — 复用 ui/ProgressBar，仅注入 Darkroom 视觉 ------------------

function gradientProgressStyles(variant: "accent" | "good"): {
  trackStyle: CSSProperties;
  barStyle: CSSProperties;
} {
  const trackStyle: CSSProperties = { background: "oklch(0.16 0.010 265)" };
  if (variant === "good") {
    return {
      trackStyle,
      barStyle: {
        background: "linear-gradient(90deg, var(--color-good), oklch(0.86 0.08 155))",
        boxShadow: "0 0 6px var(--color-good)",
      },
    };
  }
  return {
    trackStyle,
    barStyle: {
      background: "linear-gradient(90deg, var(--color-accent), var(--color-accent-2))",
      boxShadow: "0 0 6px var(--color-accent-glow)",
    },
  };
}

// -- ProjectCard --------------------------------------------------------------

interface ProjectCardProps {
  project: ProjectSummary;
  styleLabel: string;
  phaseLabels: Record<Phase, string>;
  t: TFunction;
  onDelete: () => void;
}

function ProjectCard({ project, styleLabel, phaseLabels, t, onDelete }: ProjectCardProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    function onPointerDown(e: MouseEvent) {
      const target = e.target as Node;
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      setMenuOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setMenuOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const status = asProjectStatus(project.status);
  const phase: Phase | null = status?.current_phase ?? null;
  const phaseLabel = phase ? phaseLabels[phase] : "";
  const progressPct = status ? Math.round(status.phase_progress * 100) : 0;
  const characters = status?.characters ?? { completed: 0, total: 0 };
  const scenes = status?.scenes ?? { completed: 0, total: 0 };
  const propsStat = status?.props ?? { completed: 0, total: 0 };
  const episodes =
    status?.episodes_summary ?? { total: 0, scripted: 0, in_production: 0, completed: 0 };
  const projectDisplayName = project.title || project.name;

  const { trackStyle, barStyle } = gradientProgressStyles(
    phase === "completed" ? "good" : "accent",
  );

  return (
    <article className="group relative overflow-hidden rounded-[12px] border border-hairline bg-bg-grad-a/85 transition-[transform,border-color,box-shadow] duration-150 motion-safe:hover:-translate-y-0.5 hover:border-accent/45 hover:shadow-[0_18px_40px_-22px_oklch(0_0_0_/_0.6),0_0_0_1px_var(--color-accent-soft)] focus-within:border-accent/60 focus-within:shadow-[0_0_0_2px_var(--color-accent-soft)]">
      <Link
        href={`/app/projects/${project.name}`}
        className="block w-full text-left text-text no-underline outline-none"
        aria-label={`${project.title || project.name} · ${styleLabel}${phaseLabel ? ` · ${phaseLabel}` : ""}`}
      >
        <div className="p-2.5">
          <Poster project={project} styleLabel={styleLabel} />
        </div>

        <div className="px-4 pt-1 pb-3.5">
          <div className="mb-1.5 flex items-baseline justify-between gap-2">
            <h3 className="truncate text-[17px] font-semibold tracking-tight text-text">
              {project.title || project.name}
            </h3>
            <span
              className="shrink-0 font-mono text-[9.5px] uppercase tracking-[0.08em] text-text-3"
              title={styleLabel}
            >
              {styleLabel}
            </span>
          </div>

          <div className="mb-3 flex items-center gap-2">
            <PhasePill phase={phase} label={phaseLabel} />
          </div>

          <EpisodeStrip summary={episodes} />

          <div
            className="mt-3 grid grid-cols-4 overflow-hidden rounded-[7px] border border-hairline-soft"
            style={{ background: "oklch(0.16 0.010 265 / 0.5)" }}
          >
            {(
              [
                { k: t("dashboard:lobby_card_stat_cast"), v: characters },
                { k: t("dashboard:lobby_card_stat_scene"), v: scenes },
                { k: t("dashboard:lobby_card_stat_prop"), v: propsStat },
                {
                  k: t("dashboard:lobby_card_stat_episode"),
                  v: { completed: episodes.completed, total: episodes.total },
                },
              ] as const
            ).map((cell, i) => (
              <div
                key={cell.k}
                className={
                  "px-1.5 py-2 text-center" +
                  (i < 3 ? " border-r border-hairline-soft" : "")
                }
              >
                <div className="font-mono text-[8.5px] font-bold tracking-[0.08em] text-text-3">
                  {cell.k}
                </div>
                <div className="mt-0.5 font-mono text-[11.5px] font-semibold tabular-nums text-text-2">
                  {cell.v.completed}
                  <span className="text-text-4">/{cell.v.total || "—"}</span>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-3 flex items-center gap-2.5">
            <ProgressBar
              value={progressPct}
              label={t("dashboard:lobby_now_editing_progress_label")}
              className="h-[3px] rounded-[2px] bg-transparent"
              style={trackStyle}
              barClassName="rounded-none"
              barStyle={barStyle}
            />
            <span
              className="font-mono text-[10.5px] font-semibold tabular-nums"
              style={{
                color:
                  phase === "completed" ? "var(--color-good)" : "var(--color-accent-2)",
              }}
            >
              {progressPct}%
            </span>
          </div>

          <div className="mt-2.5 flex items-center border-t border-dashed border-hairline-soft pt-2.5">
            <span className="font-mono text-[10px] uppercase tracking-[0.06em] text-text-3">
              {phaseLabel}
            </span>
          </div>
        </div>
      </Link>

      <div className="absolute right-2.5 bottom-2.5 z-[2]">
        <button
          ref={triggerRef}
          type="button"
          aria-label={`${t("dashboard:lobby_card_actions")} — ${projectDisplayName}`}
          aria-expanded={menuOpen}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setMenuOpen((v) => !v);
          }}
          className={
            "grid h-8 w-8 place-items-center rounded-md border border-hairline-soft bg-bg/70 text-text-3 backdrop-blur transition-[opacity,color,background] hover:bg-bg hover:text-text-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent " +
            (menuOpen
              ? "opacity-100"
              : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100")
          }
        >
          <MoreHorizontal className="h-3.5 w-3.5" />
        </button>
        {menuOpen ? (
          <div
            ref={menuRef}
            className="absolute right-0 bottom-[calc(100%+6px)] min-w-[148px] overflow-hidden rounded-md border border-hairline bg-bg-grad-a/95 shadow-[0_18px_40px_-22px_oklch(0_0_0_/_0.7)] backdrop-blur"
          >
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setMenuOpen(false);
                onDelete();
              }}
              aria-label={`${t("dashboard:delete_project")} — ${projectDisplayName}`}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12.5px] text-danger-2 transition-colors hover:bg-danger-soft focus-visible:bg-danger-soft focus-visible:outline-none"
            >
              <Trash2 className="h-3.5 w-3.5" />
              {t("dashboard:delete_project")}
            </button>
          </div>
        ) : null}
      </div>
    </article>
  );
}

// -- NowEditingCard -----------------------------------------------------------

interface NowEditingCardProps {
  project: ProjectSummary;
  styleLabel: string;
  phaseLabels: Record<Phase, string>;
  t: TFunction;
}

function NowEditingCard({ project, styleLabel, phaseLabels, t }: NowEditingCardProps) {
  const status = asProjectStatus(project.status);
  const phase: Phase | null = status?.current_phase ?? null;
  const phaseLabel = phase ? phaseLabels[phase] : "";
  const progressPct = status ? Math.round(status.phase_progress * 100) : 0;
  const episodes =
    status?.episodes_summary ?? { total: 0, scripted: 0, in_production: 0, completed: 0 };
  const characters = status?.characters ?? { completed: 0, total: 0 };
  const scenes = status?.scenes ?? { completed: 0, total: 0 };
  const propsStat = status?.props ?? { completed: 0, total: 0 };

  const { trackStyle, barStyle } = gradientProgressStyles(
    phase === "completed" ? "good" : "accent",
  );

  return (
    <article
      className="grid overflow-hidden rounded-[14px] border border-hairline bg-bg-grad-a"
      style={{
        gridTemplateColumns: "minmax(0, 1.4fr) minmax(0, 1fr)",
        boxShadow:
          "0 30px 80px -40px oklch(0 0 0 / 0.7), inset 0 1px 0 oklch(1 0 0 / 0.04)",
      }}
    >
      <div className="p-3.5">
        <Poster project={project} styleLabel={styleLabel} large />
      </div>
      <div className="relative flex flex-col px-7 pb-6 pt-6">
        <span
          aria-hidden
          className="font-editorial pointer-events-none absolute right-[-6px] top-2 italic"
          style={{ fontSize: 120, lineHeight: 1, color: "oklch(0.22 0.013 280)" }}
        >
          now
        </span>
        <div className="relative flex items-center gap-2.5">
          <span className="inline-flex items-center gap-1.5 font-mono text-[10px] font-bold tracking-[0.14em] text-accent-2">
            <span
              aria-hidden
              className="motion-safe:animate-pulse"
              style={{
                width: 5,
                height: 5,
                borderRadius: 3,
                background: "var(--color-accent)",
                boxShadow: "0 0 8px var(--color-accent-glow)",
              }}
            />
            {t("dashboard:lobby_continue_editing_chip")}
          </span>
        </div>
        <h3
          className="font-editorial relative mt-3 mb-1"
          style={{
            fontWeight: 400,
            fontSize: 36,
            lineHeight: 1,
            letterSpacing: "-0.012em",
            color: "var(--color-text)",
          }}
        >
          {project.title || project.name}
        </h3>
        <div className="font-editorial relative italic text-text-3" style={{ fontSize: 15 }}>
          {styleLabel}
        </div>

        <div aria-hidden className="relative my-4 h-px bg-hairline-soft" />

        <div className="relative mb-3 flex items-center gap-3.5">
          <PhasePill phase={phase} label={phaseLabel} />
          <div className="flex flex-1 items-center gap-2.5">
            <ProgressBar
              value={progressPct}
              label={t("dashboard:lobby_now_editing_progress_label")}
              className="h-[3px] rounded-[2px] bg-transparent"
              style={trackStyle}
              barClassName="rounded-none"
              barStyle={barStyle}
            />
            <span className="font-mono text-[11px] font-semibold tabular-nums text-accent-2">
              {progressPct}%
            </span>
          </div>
        </div>

        <div
          className="relative grid overflow-hidden rounded-[8px]"
          style={{
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: 1,
            background: "var(--color-hairline-soft)",
          }}
        >
          {[
            {
              k: t("dashboard:lobby_now_editing_phase_label"),
              v: phaseLabel || "—",
              sub: t("dashboard:lobby_now_editing_episodes_value", {
                completed: episodes.completed,
                total: episodes.total,
              }),
            },
            {
              k: t("dashboard:characters"),
              v: `${characters.completed} / ${characters.total || "—"}`,
              sub: `${t("dashboard:scenes")} ${scenes.completed}/${scenes.total || "—"}`,
            },
            {
              k: t("dashboard:props"),
              v: `${propsStat.completed} / ${propsStat.total || "—"}`,
              sub: `${t("dashboard:lobby_now_editing_progress_label")} ${progressPct}%`,
            },
          ].map((cell) => (
            <div
              key={cell.k}
              className="px-3.5 py-3"
              style={{ background: "oklch(0.16 0.010 265 / 0.6)" }}
            >
              <div className="font-mono text-[9px] font-bold uppercase tracking-[0.1em] text-text-3">
                {cell.k}
              </div>
              <div className="mt-1 text-[14px] font-semibold tracking-tight text-text">
                {cell.v}
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-text-3">{cell.sub}</div>
            </div>
          ))}
        </div>

        <div className="flex-1" />
        <div className="relative mt-4 flex justify-end">
          <Link
            href={`/app/projects/${project.name}`}
            className="inline-flex items-center gap-2 rounded-[7px] px-4 py-2.5 text-[12px] font-semibold no-underline transition-transform motion-safe:hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            style={ACCENT_BUTTON_STYLE}
          >
            {phase === "completed"
              ? t("dashboard:lobby_open_workspace_completed")
              : t("dashboard:lobby_open_workspace")}
            <span aria-hidden>→</span>
          </Link>
        </div>
      </div>
    </article>
  );
}

// -- PlaceholderTile (新建项目 / 导入 ZIP) -----------------------------------

interface PlaceholderTileProps {
  onClick: () => void;
  title: string;
  kicker: string;
  icon: ReactNode;
  ariaLabel?: string;
}

function PlaceholderTile({ onClick, title, kicker, icon, ariaLabel }: PlaceholderTileProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group relative flex h-full min-h-[380px] flex-col overflow-hidden rounded-[12px] border border-dashed border-hairline-strong bg-bg-grad-a/55 text-left transition-colors hover:border-accent/55 hover:bg-bg-grad-a/75 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      aria-label={ariaLabel ?? title}
    >
      <div className="p-2.5">
        <div
          className="relative grid place-items-center overflow-hidden rounded-[6px] border border-dashed border-hairline"
          style={{
            aspectRatio: "2 / 1",
            background:
              "radial-gradient(120% 80% at 30% 30%, oklch(0.26 0.04 290 / 0.5) 0%, transparent 60%), oklch(0.18 0.011 265 / 0.55)",
          }}
        >
          <div className="flex flex-col items-center gap-2.5 transition-transform motion-safe:group-hover:-translate-y-0.5">
            <span
              aria-hidden
              className="grid h-12 w-12 place-items-center rounded-[12px]"
              style={{
                background:
                  "linear-gradient(180deg, oklch(0.30 0.04 290), oklch(0.22 0.02 280))",
                border: "1px solid oklch(0.76 0.09 295 / 0.4)",
                boxShadow:
                  "inset 0 1px 0 oklch(1 0 0 / 0.06), 0 8px 22px -14px var(--color-accent)",
                color: "var(--color-accent-2)",
              }}
            >
              {icon}
            </span>
            <div className="text-center">
              <div className="text-[15px] font-semibold tracking-tight text-text-2 transition-colors group-hover:text-text">
                {title}
              </div>
              <div className="mt-0.5 font-mono text-[9.5px] font-semibold uppercase tracking-[0.14em] text-text-3">
                {kicker}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div aria-hidden className="space-y-3 px-4 pt-1 pb-3.5">
        <div className="flex items-center justify-between gap-2">
          <span className="block h-3 w-1/2 rounded-[3px] bg-hairline/85" />
          <span className="block h-2 w-12 rounded-[3px] bg-hairline/65" />
        </div>
        <span className="inline-block h-[18px] w-16 rounded-full border border-dashed border-hairline" />
        <div className="flex gap-[3px]">
          {Array.from({ length: 8 }).map((_, i) => (
            <span key={i} className="h-[3px] flex-1 rounded-[1.5px] bg-hairline/65" />
          ))}
        </div>
        <div
          className="grid grid-cols-4 overflow-hidden rounded-[7px] border border-dashed border-hairline"
          style={{ background: "oklch(0.16 0.010 265 / 0.45)" }}
        >
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className={"px-1.5 py-2.5" + (i < 3 ? " border-r border-dashed border-hairline" : "")}
            >
              <span className="mx-auto block h-1.5 w-8 rounded-[1.5px] bg-hairline/75" />
              <span className="mx-auto mt-1.5 block h-2 w-6 rounded-[1.5px] bg-hairline/55" />
            </div>
          ))}
        </div>
        <div className="flex items-center gap-2.5">
          <span className="h-[3px] flex-1 rounded-[1.5px] bg-hairline/55" />
          <span className="h-2 w-7 rounded-[3px] bg-hairline/70" />
        </div>
      </div>
    </button>
  );
}

function NewProjectTile({ onClick, t }: { onClick: () => void; t: TFunction }) {
  return (
    <PlaceholderTile
      onClick={onClick}
      title={t("dashboard:lobby_new_project_title")}
      kicker={t("dashboard:lobby_new_project_kicker")}
      icon={<Plus className="h-6 w-6" />}
    />
  );
}

// -- TopBar -------------------------------------------------------------------

interface TopBarProps {
  searchValue: string;
  onSearch: (v: string) => void;
  onImport: () => void;
  onCreate: () => void;
  onSettings: () => void;
  onAssets: () => void;
  onOpenClaw: () => void;
  importing: boolean;
  configIncomplete: boolean;
  searchInputRef: React.RefObject<HTMLInputElement | null>;
}

function TopBar({
  searchValue,
  onSearch,
  onImport,
  onCreate,
  onSettings,
  onAssets,
  onOpenClaw,
  importing,
  configIncomplete,
  searchInputRef,
}: TopBarProps) {
  const { t } = useTranslation(["common", "dashboard", "assets"]);
  return (
    <div
      className="sticky top-0 z-30"
      style={{
        background:
          "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.55), oklch(0.15 0.010 265 / 0.45))",
        backdropFilter: "blur(28px) saturate(1.5)",
        WebkitBackdropFilter: "blur(28px) saturate(1.5)",
        borderBottom: "1px solid oklch(1 0 0 / 0.06)",
        boxShadow:
          "inset 0 1px 0 oklch(1 0 0 / 0.05), 0 6px 24px -12px oklch(0 0 0 / 0.45)",
      }}
    >
      <div className="mx-auto flex max-w-[1320px] items-center gap-4 px-6 py-3">
        <div className="flex items-center gap-2.5">
          <img
            src="/android-chrome-192x192.png"
            alt="ArcReel"
            className="h-8 w-8 rounded-lg"
          />
          <span
            className="font-sans text-[17px] font-medium tracking-[-0.012em] text-text"
            aria-hidden
          >
            ArcReel
          </span>
        </div>

        <label className="ml-2 flex w-[min(420px,100%)] items-center gap-2 rounded-lg border border-hairline-soft bg-bg/55 px-3 py-1.5 transition-colors focus-within:border-accent/60">
            <Search className="h-3.5 w-3.5 text-text-3" />
            <input
              ref={searchInputRef}
              type="search"
              name="q"
              aria-label={t("dashboard:search_projects")}
              value={searchValue}
              onChange={(e) => onSearch(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              enterKeyHint="search"
              inputMode="search"
              aria-keyshortcuts="Meta+K Control+K"
              placeholder={t("dashboard:lobby_search_placeholder")}
              className="flex-1 bg-transparent text-[12.5px] text-text placeholder:text-text-3 outline-none"
            />
            <kbd
              aria-hidden
              className="rounded border border-hairline-soft px-1.5 py-px font-mono text-[9.5px] text-text-3"
            >
              {t("dashboard:lobby_search_kbd")}
            </kbd>
        </label>

        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={onAssets}
            className="inline-flex items-center gap-1.5 rounded-[7px] border border-accent/25 bg-accent-dim px-3 py-1.5 text-[12px] text-text-2 transition-colors hover:border-accent/50 hover:bg-accent-soft hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            title={t("assets:library_title")}
          >
            <Library className="h-3.5 w-3.5" />
            {t("assets:library_title")}
          </button>
          <span aria-hidden className="mx-1 h-5 w-px bg-hairline-soft" />
          <button
            type="button"
            onClick={onImport}
            disabled={importing}
            className="inline-flex items-center gap-1.5 rounded-[7px] border border-hairline bg-bg-grad-a/50 px-3 py-1.5 text-[12px] text-text-2 transition-colors hover:border-hairline-strong hover:bg-bg-grad-a focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-60"
          >
            {importing ? (
              <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" />
            ) : (
              <Upload className="h-3.5 w-3.5" />
            )}
            {importing ? t("dashboard:importing") : t("dashboard:import_zip")}
          </button>
          <button
            type="button"
            onClick={onCreate}
            className="inline-flex items-center gap-1.5 rounded-[7px] px-3.5 py-1.5 text-[12px] font-semibold transition-transform motion-safe:hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            style={ACCENT_BUTTON_STYLE}
          >
            <Plus className="h-3.5 w-3.5" />
            {t("dashboard:create_project")}
          </button>
          <span aria-hidden className="mx-1 h-5 w-px bg-hairline-soft" />
          <button
            type="button"
            onClick={onOpenClaw}
            className="rounded-md px-2 py-1.5 text-sm text-text-3 transition-colors hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            title={t("dashboard:openclaw")}
            aria-label={t("dashboard:openclaw")}
          >
            <span aria-hidden>🦞</span>
          </button>
          <button
            type="button"
            onClick={onSettings}
            className="relative rounded-md p-1.5 text-text-3 transition-colors hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            title={t("settings")}
            aria-label={t("settings")}
          >
            <Settings className="h-4 w-4" aria-hidden />
            {configIncomplete ? (
              <span
                aria-label={t("config_incomplete")}
                className="absolute right-0.5 top-0.5 h-2 w-2 rounded-full bg-rose-500"
              />
            ) : null}
          </button>
        </div>
      </div>
    </div>
  );
}

// -- HeroStrip ----------------------------------------------------------------

function formatDateKicker(d: Date, locale: string): string {
  try {
    return new Intl.DateTimeFormat(locale, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      weekday: "short",
    }).format(d);
  } catch {
    return d.toISOString().slice(0, 10);
  }
}

interface HeroStripProps {
  totals: {
    total: number;
    production: number;
    completed: number;
    drafts: number;
    episodesCompleted: number;
    episodesInProduction: number;
  };
  t: TFunction;
}

function HeroStrip({ totals, t }: HeroStripProps) {
  const { i18n } = useTranslation();
  const greetingKey = useMemo<GreetingKey>(() => getGreetingKey(), []);
  const dateLine = useMemo(
    () => formatDateKicker(new Date(), i18n.language || "zh"),
    [i18n.language],
  );

  let subtitle: string;
  if (totals.production > 0) {
    subtitle = t("dashboard:lobby_hero_subtitle_active", { count: totals.production });
  } else if (totals.total > 0) {
    subtitle = t("dashboard:lobby_hero_subtitle_quiet");
  } else {
    subtitle = t("dashboard:lobby_hero_subtitle_idle");
  }
  const summaryLine =
    totals.total === 0
      ? t("dashboard:lobby_hero_summary_idle")
      : t("dashboard:lobby_hero_summary", {
          completed: totals.episodesCompleted,
          inProduction: totals.episodesInProduction,
        });

  const stats: Array<{ key: string; label: string; value: number; tone: CSSProperties }> = [
    {
      key: "total",
      label: t("dashboard:lobby_stat_total"),
      value: totals.total,
      tone: { color: "var(--color-text)" },
    },
    {
      key: "prod",
      label: t("dashboard:lobby_stat_production"),
      value: totals.production,
      tone: { color: "var(--color-accent-2)" },
    },
    {
      key: "draft",
      label: t("dashboard:lobby_stat_drafts"),
      value: totals.drafts,
      tone: { color: "oklch(0.86 0.06 75)" },
    },
    {
      key: "done",
      label: t("dashboard:lobby_stat_completed"),
      value: totals.completed,
      tone: { color: "var(--color-good)" },
    },
  ];

  return (
    <div className="mx-auto flex max-w-[1320px] items-stretch justify-between gap-6 px-6 pb-5 pt-6">
      <div className="min-w-0 flex-1">
        <h1
          className="font-editorial m-0"
          style={{
            fontSize: 46,
            fontWeight: 400,
            lineHeight: 1.22,
            letterSpacing: "-0.012em",
            color: "var(--color-text)",
          }}
        >
          <Typewriter
            once="lobby-hero"
            segments={
              [
                { text: t(`dashboard:${greetingKey}`), after: <br /> },
                {
                  text: subtitle,
                  style: { fontStyle: "italic", color: "var(--color-accent-2)" },
                },
              ] satisfies TypewriterSegment[]
            }
          />
        </h1>
        <p className="m-0 mt-2.5 max-w-[560px] text-[13px] leading-[1.55] text-text-3">
          {summaryLine}
        </p>
      </div>
      <div className="flex flex-col items-end justify-between gap-2.5">
        <div className="mt-1.5 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-accent-2">
          · {t("dashboard:lobby_hero_eyebrow")} · {dateLine}
        </div>
        <div
          className="flex items-stretch overflow-hidden rounded-[10px] border border-hairline-soft"
          style={{ background: "oklch(0.16 0.010 265 / 0.4)" }}
        >
          {stats.map((s, i) => (
            <div
              key={s.key}
              className={
                "px-4 py-2.5" +
                (i < stats.length - 1 ? " border-r border-hairline-soft" : "")
              }
            >
              <div className="font-mono text-[9px] font-bold tracking-[0.14em] text-text-3">
                {s.label}
              </div>
              <div
                className="font-editorial mt-0.5 tabular-nums"
                style={{
                  fontSize: 30,
                  fontWeight: 400,
                  lineHeight: 1,
                  letterSpacing: "-0.012em",
                  ...s.tone,
                }}
              >
                {s.value}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// -- FilterPills --------------------------------------------------------------

interface FilterPillsProps {
  active: PhaseFilter;
  onChange: (next: PhaseFilter) => void;
  counts: Record<Phase, number> & { all: number };
  phaseLabels: Record<Phase, string>;
  t: TFunction;
}

function FilterPills({ active, onChange, counts, phaseLabels, t }: FilterPillsProps) {
  const pills: Array<{ key: PhaseFilter; label: string; n: number }> = [
    { key: "all", label: t("dashboard:lobby_filter_all"), n: counts.all },
    { key: "production", label: phaseLabels.production, n: counts.production },
    { key: "scripting", label: phaseLabels.scripting, n: counts.scripting },
    { key: "worldbuilding", label: phaseLabels.worldbuilding, n: counts.worldbuilding },
    { key: "completed", label: phaseLabels.completed, n: counts.completed },
    { key: "setup", label: phaseLabels.setup, n: counts.setup },
  ];

  return (
    <div
      className="sticky z-20 border-b border-hairline backdrop-blur-md"
      style={{
        top: "var(--lobby-topbar-h, 57px)",
        background:
          "linear-gradient(180deg, oklch(0.18 0.010 265 / 0.94), oklch(0.17 0.010 265 / 0.90))",
        backdropFilter: "blur(16px) saturate(1.1)",
        borderTopWidth: 1,
        borderTopColor: "var(--color-hairline-soft)",
      }}
    >
      <div className="mx-auto flex max-w-[1320px] items-center gap-1.5 px-6 py-2.5">
        {pills.map((c) => {
          const isActive = active === c.key;
          return (
            <button
              key={c.key}
              type="button"
              onClick={() => onChange(c.key)}
              aria-pressed={isActive}
              className={
                "inline-flex items-center rounded-full px-3 py-1 text-[11.5px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent " +
                (isActive
                  ? "border border-accent/35 bg-accent/30 text-text"
                  : "border border-hairline-soft bg-transparent text-text-3 hover:border-hairline hover:text-text-2")
              }
            >
              {c.label}
              <span
                className={
                  "ml-1.5 font-mono tabular-nums " +
                  (isActive ? "text-accent-2" : "text-text-4")
                }
              >
                {c.n}
              </span>
            </button>
          );
        })}
        <div className="flex-1" />
        <span className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-text-3">
          {t("dashboard:lobby_sort_recent")}
        </span>
      </div>
    </div>
  );
}

// -- ProjectsPage -------------------------------------------------------------

export function ProjectsPage() {
  const { t, i18n } = useTranslation(["common", "dashboard", "assets"]);
  const [, navigate] = useLocation();
  const {
    projects,
    projectsLoading,
    showCreateModal,
    setProjects,
    setProjectsLoading,
    setShowCreateModal,
  } = useProjectsStore();

  const [importingProject, setImportingProject] = useState(false);
  const [conflictProject, setConflictProject] = useState<string | null>(null);
  const [conflictFile, setConflictFile] = useState<File | null>(null);
  type ImportDiagnosticsState =
    | { source: "success"; diagnostics: ImportFailureDiagnostics; navigateTo: string }
    | { source: "failure"; diagnostics: ImportFailureDiagnostics };
  const [importDiagnostics, setImportDiagnostics] =
    useState<ImportDiagnosticsState | null>(null);
  const [showOpenClaw, setShowOpenClaw] = useState(false);
  const [deletingProject, setDeletingProject] = useState<ProjectSummary | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [phaseFilter, setPhaseFilter] = useState<PhaseFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const importInputRef = useRef<HTMLInputElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const isConfigComplete = useConfigStatusStore((s) => s.isComplete);

  const phaseLabels = useMemo<Record<Phase, string>>(
    () => ({
      setup: t("dashboard:phase_setup"),
      worldbuilding: t("dashboard:phase_worldbuilding"),
      scripting: t("dashboard:phase_scripting"),
      production: t("dashboard:phase_production"),
      completed: t("dashboard:phase_completed"),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- t reference rotates with i18n.language
    [i18n.language],
  );

  const fetchProjects = useCallback(async () => {
    setProjectsLoading(true);
    try {
      const res = await API.listProjects();
      setProjects(res.projects);
    } finally {
      setProjectsLoading(false);
    }
  }, [setProjects, setProjectsLoading]);

  useEffect(() => {
    void fetchProjects();
  }, [fetchProjects]);

  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    await doImport(file);
    e.target.value = "";
  };

  const doImport = async (file: File, policy: ImportConflictPolicy = "prompt") => {
    setImportingProject(true);
    try {
      const result = await API.importProject(file, policy);
      setConflictProject(null);
      setConflictFile(null);
      setImportDiagnostics(null);
      await fetchProjects();

      const autoFixedCount = result.diagnostics.auto_fixed.length;
      const warningCount = result.diagnostics.warnings.length;
      const navigateTo = `/app/projects/${result.project_name}`;
      if (warningCount > 0 || autoFixedCount > 0) {
        useAppStore
          .getState()
          .pushToast(
            autoFixedCount > 0
              ? t("dashboard:import_auto_fixed", {
                  title: result.project.title || result.project_name,
                  count: autoFixedCount,
                })
              : t("dashboard:import_success", {
                  title: result.project.title || result.project_name,
                }),
            "success",
          );
        setImportDiagnostics({
          source: "success",
          diagnostics: {
            blocking: [],
            auto_fixable: result.diagnostics.auto_fixed,
            warnings: result.diagnostics.warnings,
          },
          navigateTo,
        });
        return;
      }
      navigate(navigateTo);
    } catch (err) {
      const error = err as Error & {
        status?: number;
        conflict_project_name?: string;
        diagnostics?: ImportFailureDiagnostics;
      };

      if (
        error.status === 409 &&
        error.conflict_project_name &&
        policy === "prompt"
      ) {
        setConflictFile(file);
        setConflictProject(error.conflict_project_name);
        return;
      }

      if (error.diagnostics) {
        setImportDiagnostics({ source: "failure", diagnostics: error.diagnostics });
      } else {
        useAppStore
          .getState()
          .pushToast(`${t("dashboard:import_failed")}: ${error.message}`, "warning");
      }
    } finally {
      setImportingProject(false);
    }
  };

  const handleDeleteProject = async () => {
    if (!deletingProject) return;
    const projectDisplayName = deletingProject.title || deletingProject.name;
    setDeleteLoading(true);
    try {
      await API.deleteProject(deletingProject.name);
      await fetchProjects();
      useAppStore.getState().pushToast(t("common:deleted"), "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(
          `${t("dashboard:delete_failed")}[${projectDisplayName}] ${errMsg(err)}`,
          "warning",
        );
    } finally {
      setDeleteLoading(false);
      setDeletingProject(null);
    }
  };

  const phaseCounts = useMemo(() => {
    const out: Record<Phase, number> & { all: number } = {
      all: 0,
      setup: 0,
      worldbuilding: 0,
      scripting: 0,
      production: 0,
      completed: 0,
    };
    for (const p of projects) {
      out.all += 1;
      const status = asProjectStatus(p.status);
      if (status) out[status.current_phase] += 1;
    }
    return out;
  }, [projects]);

  const totals = useMemo(() => {
    let production = 0;
    let completed = 0;
    let drafts = 0;
    let episodesCompleted = 0;
    let episodesInProduction = 0;
    for (const p of projects) {
      const s = asProjectStatus(p.status);
      if (!s) continue;
      if (s.current_phase === "production") production += 1;
      else if (s.current_phase === "completed") completed += 1;
      else drafts += 1;
      episodesCompleted += s.episodes_summary.completed;
      episodesInProduction += s.episodes_summary.in_production;
    }
    return {
      total: projects.length,
      production,
      completed,
      drafts,
      episodesCompleted,
      episodesInProduction,
    };
  }, [projects]);

  const styleLabels = useMemo(() => {
    const map: Record<string, string> = {};
    for (const p of projects) map[p.name] = styleLabelOf(p, t);
    return map;
  }, [projects, t]);

  const filteredProjects = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return projects.filter((p) => {
      const s = asProjectStatus(p.status);
      if (phaseFilter !== "all") {
        if (!s || s.current_phase !== phaseFilter) return false;
      }
      if (!q) return true;
      const phaseLabel = s ? phaseLabels[s.current_phase] : "";
      return `${p.title || ""} ${p.name} ${phaseLabel}`.toLowerCase().includes(q);
    });
  }, [projects, phaseFilter, searchQuery, phaseLabels]);

  const featuredCandidate = useMemo(() => pickFeaturedProject(projects), [projects]);
  const featured =
    phaseFilter === "all" && !searchQuery.trim() ? featuredCandidate : null;

  const restProjects = useMemo(
    () =>
      featured
        ? filteredProjects.filter((p) => p.name !== featured.name)
        : filteredProjects,
    [featured, filteredProjects],
  );

  return (
    <div
      className="relative min-h-screen text-text"
      style={
        {
          // FilterPills 的 sticky top 读这个变量；TopBar = logo h-8 (32) + py-3 (24) + 1px border
          "--lobby-topbar-h": "57px",
          background:
            "radial-gradient(1100px 540px at 8% -10%, oklch(0.32 0.05 295 / 0.28), transparent 55%), radial-gradient(900px 500px at 100% 110%, oklch(0.26 0.04 260 / 0.25), transparent 55%), linear-gradient(180deg, var(--color-bg-grad-a), var(--color-bg-grad-b))",
        } as CSSProperties
      }
    >
      <TopBar
        searchValue={searchQuery}
        onSearch={setSearchQuery}
        onImport={() => importInputRef.current?.click()}
        onCreate={() => setShowCreateModal(true)}
        onSettings={() => navigate("/app/settings")}
        onAssets={() => {
          rememberAssetLibraryReturnTo(window.location.pathname);
          navigate("/app/assets");
        }}
        onOpenClaw={() => setShowOpenClaw(true)}
        importing={importingProject}
        configIncomplete={!isConfigComplete}
        searchInputRef={searchInputRef}
      />
      <input
        ref={importInputRef}
        type="file"
        accept=".zip,application/zip"
        aria-label={t("dashboard:import_project_file_aria")}
        onChange={voidPromise(handleImport)}
        className="hidden"
      />

      <HeroStrip totals={totals} t={t} />

      {projects.length > 0 ? (
        <FilterPills
          active={phaseFilter}
          onChange={setPhaseFilter}
          counts={phaseCounts}
          phaseLabels={phaseLabels}
          t={t}
        />
      ) : null}

      <main className="mx-auto max-w-[1320px] px-6 pt-6 pb-16">
        {projectsLoading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-6 w-6 motion-safe:animate-spin text-accent" />
            <span className="ml-2 text-text-3">{t("dashboard:loading_projects")}</span>
          </div>
        ) : projects.length === 0 ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <NewProjectTile onClick={() => setShowCreateModal(true)} t={t} />
          </div>
        ) : (
          <>
            {featured ? (
              <section className="mb-7" aria-labelledby="lobby-now-editing-heading">
                <div className="mb-3 flex items-baseline justify-between">
                  <h2
                    id="lobby-now-editing-heading"
                    className="m-0 font-mono text-[12.5px] font-semibold uppercase tracking-[0.06em] text-text-2"
                  >
                    · {t("dashboard:lobby_now_editing_eyebrow")}
                  </h2>
                </div>
                <NowEditingCard
                  project={featured}
                  styleLabel={styleLabels[featured.name] ?? ""}
                  phaseLabels={phaseLabels}
                  t={t}
                />
              </section>
            ) : null}

            {filteredProjects.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-text-3">
                <p className="text-lg text-text">{t("dashboard:lobby_no_filter_match")}</p>
                <p className="mt-1 text-sm">{t("dashboard:lobby_no_filter_match_hint")}</p>
                <button
                  type="button"
                  onClick={() => {
                    setPhaseFilter("all");
                    setSearchQuery("");
                  }}
                  className="mt-4 rounded-md border border-hairline px-3 py-1.5 text-[12px] text-text-2 hover:border-accent/40 hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {t("dashboard:lobby_clear_filters")}
                </button>
              </div>
            ) : (
              <section aria-labelledby="lobby-library-heading">
                <div className="mb-3 flex items-baseline justify-between">
                  <h2
                    id="lobby-library-heading"
                    className="m-0 font-mono text-[12.5px] font-semibold uppercase tracking-[0.06em] text-text-2"
                  >
                    · {t("dashboard:lobby_library_eyebrow")}
                  </h2>
                  <span className="font-mono text-[10.5px] tabular-nums text-text-3">
                    {t("dashboard:lobby_library_count", { count: restProjects.length })}
                  </span>
                </div>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {restProjects.map((project) => (
                    <ProjectCard
                      key={project.name}
                      project={project}
                      styleLabel={styleLabels[project.name] ?? ""}
                      phaseLabels={phaseLabels}
                      t={t}
                      onDelete={() => setDeletingProject(project)}
                    />
                  ))}
                  <NewProjectTile onClick={() => setShowCreateModal(true)} t={t} />
                </div>
              </section>
            )}
          </>
        )}
      </main>

      {conflictProject && conflictFile && (
        <ConflictDialog
          projectName={conflictProject}
          importing={importingProject}
          onConfirm={(policy) => voidCall(doImport(conflictFile, policy))}
          onCancel={() => {
            setConflictProject(null);
            setConflictFile(null);
          }}
        />
      )}

      {importDiagnostics && (
        <ArchiveDiagnosticsDialog
          title={t(
            importDiagnostics.source === "failure"
              ? "dashboard:import_failure_diagnostics"
              : "dashboard:import_diagnostics",
          )}
          description={t(
            importDiagnostics.source === "failure"
              ? "dashboard:import_failure_with_diagnostics"
              : "dashboard:import_success_with_diagnostics",
          )}
          sections={[
            {
              key: "blocking",
              title: t("dashboard:blocking_issues"),
              severity: "blocking",
              items: importDiagnostics.diagnostics.blocking,
            },
            {
              key: "auto_fixed",
              title: t("dashboard:auto_fixed_issues"),
              severity: "auto_fixed",
              items: importDiagnostics.diagnostics.auto_fixable,
            },
            {
              key: "warnings",
              title: t("dashboard:diagnostics_warnings"),
              severity: "warnings",
              items: importDiagnostics.diagnostics.warnings,
            },
          ]}
          onClose={() => {
            const target =
              importDiagnostics.source === "success" ? importDiagnostics.navigateTo : null;
            setImportDiagnostics(null);
            if (target) navigate(target);
          }}
        />
      )}

      {showOpenClaw && <OpenClawModal onClose={() => setShowOpenClaw(false)} />}
      {showCreateModal && <CreateProjectModal />}

      {deletingProject && (
        <DeleteConfirmDialog
          title={deletingProject.title || deletingProject.name}
          loading={deleteLoading}
          onCancel={() => setDeletingProject(null)}
          onConfirm={voidPromise(handleDeleteProject)}
        />
      )}
    </div>
  );
}

// -- DeleteConfirmDialog ------------------------------------------------------

function DeleteConfirmDialog({
  title,
  loading,
  onCancel,
  onConfirm,
}: {
  title: string;
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation(["common", "dashboard"]);
  const dialogRef = useRef<HTMLDivElement>(null);
  useEscapeClose(onCancel, !loading);
  useFocusTrap(dialogRef, true);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="lobby-delete-title"
        aria-describedby="lobby-delete-desc"
        className="w-full max-w-md overflow-hidden rounded-2xl border border-hairline bg-bg-grad-a p-6 shadow-2xl"
      >
        <div className="flex items-start gap-4">
          <div
            aria-hidden
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-red-500/10 text-red-500"
          >
            <AlertTriangle className="h-6 w-6" />
          </div>
          <div className="space-y-2">
            <h2 id="lobby-delete-title" className="text-lg font-semibold text-text">
              {t("dashboard:delete_project")}
            </h2>
            <p id="lobby-delete-desc" className="text-sm leading-6 text-text-3">
              {t("dashboard:confirm_delete_project", { title })}
            </p>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={loading}
            className="rounded-lg border border-hairline px-4 py-2 text-sm text-text-2 transition-colors hover:border-hairline-strong hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-60"
          >
            {t("cancel")}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading && <Loader2 className="h-4 w-4 motion-safe:animate-spin" />}
            {loading ? t("dashboard:deleting_project") : t("dashboard:delete_project")}
          </button>
        </div>
      </div>
    </div>
  );
}

// -- ConflictDialog -----------------------------------------------------------

function ConflictDialog({
  projectName,
  importing,
  onConfirm,
  onCancel,
}: {
  projectName: string;
  importing: boolean;
  onConfirm: (policy: "overwrite" | "rename") => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation(["common", "dashboard"]);
  const dialogRef = useRef<HTMLDivElement>(null);
  useEscapeClose(onCancel, !importing);
  useFocusTrap(dialogRef, true);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="lobby-conflict-title"
        className="w-full max-w-lg overflow-hidden rounded-2xl border border-hairline bg-bg-grad-a p-6 shadow-2xl"
      >
        <div className="flex items-start gap-4">
          <div
            aria-hidden
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-amber-500/10 text-amber-500"
          >
            <AlertTriangle className="h-6 w-6" />
          </div>
          <div className="space-y-2">
            <h2 id="lobby-conflict-title" className="text-lg font-semibold text-text">
              {t("dashboard:duplicate_project_id")}
            </h2>
            <p className="text-sm leading-6 text-text-3">
              {t("dashboard:id_intended_hint")}
              <span className="mx-1 rounded bg-bg/70 px-1.5 py-0.5 font-mono text-text">
                {projectName}
              </span>
              {t("dashboard:already_exists_conflict_hint")}
            </p>
          </div>
        </div>

        <div className="mt-5 grid gap-3">
          <button
            type="button"
            onClick={() => onConfirm("overwrite")}
            disabled={importing}
            aria-label={t("dashboard:overwrite_existing")}
            className="flex w-full items-center justify-between rounded-xl border border-red-400/25 bg-red-500/10 px-4 py-3 text-left text-sm text-red-100 transition-colors hover:border-red-300/40 hover:bg-red-500/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <span>
              <span className="block font-medium">{t("dashboard:overwrite_existing")}</span>
              <span className="mt-1 block text-xs text-red-200/80">
                {t("dashboard:overwrite_hint")}
              </span>
            </span>
            {importing && <Loader2 className="h-4 w-4 motion-safe:animate-spin" />}
          </button>

          <button
            type="button"
            onClick={() => onConfirm("rename")}
            disabled={importing}
            aria-label={t("dashboard:auto_rename_import")}
            className="flex w-full items-center justify-between rounded-xl border border-accent/25 bg-accent-dim px-4 py-3 text-left text-sm text-text transition-colors hover:border-accent/40 hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-60"
          >
            <span>
              <span className="block font-medium">{t("dashboard:auto_rename_import")}</span>
              <span className="mt-1 block text-xs text-text-3">
                {t("dashboard:rename_hint")}
              </span>
            </span>
            {importing && <Loader2 className="h-4 w-4 motion-safe:animate-spin" />}
          </button>
        </div>

        <div className="mt-5 flex justify-end">
          <button
            type="button"
            onClick={onCancel}
            disabled={importing}
            className="rounded-lg border border-hairline px-4 py-2 text-sm text-text-2 transition-colors hover:border-hairline-strong hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-60"
          >
            {t("cancel")}
          </button>
        </div>
      </div>
    </div>
  );
}
