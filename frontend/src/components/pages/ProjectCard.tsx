/**
 * 项目大厅的卡片及其零件（海报 / 集进度徽标 / 分集条 / 进度条配色）。
 *
 * 从 ProjectsPage 拆出来，是因为引导的演示卡也要用同一张卡 —— 「项目推进后长这样」
 * 这句话只有在演示卡与真实卡片是同一份实现时才不会随时间说谎。演示卡走 `readOnly`
 * 形态：点进去是只读工作台，卡上不带操作菜单。
 */

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Link } from "wouter";
import { MoreHorizontal, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { getProjectDisplayName } from "@/utils/project-display";
import { hashHue } from "@/components/shared/darkroom-tokens";
import type { ArtifactCount, ProjectStatus, ProjectSummary } from "@/types";

interface ProgressTone {
  dot: string;
  text: string;
  glow: string;
}

/** 集进度徽标的三种色调：尚未建集、制作中、项目完成。 */
const PROGRESS_TONE: Record<ProjectProgress, ProgressTone> = {
  empty: {
    dot: "oklch(0.64 0.020 265)",
    text: "oklch(0.78 0.010 265)",
    glow: "transparent",
  },
  in_progress: {
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

/**
 * 项目的集进度：没有集、制作中、项目完成。不是流水线阶段。项目完成要求已有的集全部完成，且整本源文
 * 没有未规划成集的原文，与项目顶栏的「全部完成」同一口径。源文是否规划完由账本命令记进项目摘要，大厅不读源文。
 */
export type ProjectProgress = "empty" | "in_progress" | "completed";

export function projectProgress(status: ProjectStatus | null): ProjectProgress {
  const episodes = status?.episodes_summary;
  if (!episodes || episodes.total === 0) return "empty";
  return episodes.completed >= episodes.total && !status.source_remaining ? "completed" : "in_progress";
}

/** 集进度的一句话：「已完成 N / M 集」，没有集时「尚未建集」。 */
export function useProgressLabel(): (status: ProjectStatus | null) => string {
  const { t } = useTranslation("dashboard");
  return useCallback(
    (status: ProjectStatus | null) => {
      const episodes = status?.episodes_summary;
      if (!episodes || episodes.total === 0) return t("lobby_card_no_episodes");
      return t("lobby_card_progress", { completed: episodes.completed, total: episodes.total });
    },
    [t],
  );
}

const POSTER_FX_STYLE: CSSProperties = {
  background:
    "linear-gradient(115deg, oklch(1 0 0 / 0.18) 0%, transparent 30%), linear-gradient(295deg, oklch(0 0 0 / 0.55) 0%, transparent 45%)",
};


const POSTER_SPROCKET_STYLE: CSSProperties = {
  background:
    "repeating-linear-gradient(0deg, oklch(0 0 0 / 0.6) 0 6px, transparent 6px 12px)",
};

export function asProjectStatus(s: ProjectSummary["status"]): ProjectStatus | null {
  return s && "episodes_summary" in s ? (s as ProjectStatus) : null;
}

// -- Poster -------------------------------------------------------------------

interface PosterProps {
  project: ProjectSummary;
  styleLabel: string;
  large?: boolean;
}

export function Poster({ project, styleLabel, large = false }: PosterProps) {
  const { t } = useTranslation("dashboard");
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
          {getProjectDisplayName(project.title, t("untitled_project"))}
        </div>
      </div>
    </div>
  );
}

// -- 待修复标记 ---------------------------------------------------------------

/** 迁移未跑完的项目在列表上的标记。大厅两张卡（常规卡与「正在编辑」卡）共用一份。 */
export function NeedsRepairPill() {
  const { t } = useTranslation("dashboard");
  return (
    <span
      className="inline-flex items-center rounded-full border px-2 py-[2px] font-mono text-[10px] font-semibold uppercase tracking-[0.06em]"
      style={{
        color: "var(--warn)",
        borderColor: "color-mix(in oklab, var(--warn) 30%, transparent)",
        background: "color-mix(in oklab, var(--warn) 10%, transparent)",
      }}
    >
      {t("lobby_card_needs_repair")}
    </span>
  );
}

/**
 * 失败原因直接显示，不塞 `title`：tooltip 在触摸设备上打不开，而这行是用户在列表上
 * 判断该不该进项目修的唯一线索。完整原文在项目内的条幅上。
 */
export function RepairReasonLine({ reason }: { reason: string | null }) {
  if (!reason) return null;
  return (
    <p className="mb-3 line-clamp-2 break-words font-mono text-[10.5px] leading-[1.45] text-muted-foreground">
      {reason}
    </p>
  );
}

/** 只有确实被阻断的项目才有原因可显示——健康项目上的残留原因不展示。 */
export function repairReasonOf(status: ProjectStatus | null): string | null {
  return status?.needs_repair ? (status.repair_reason ?? null) : null;
}

// -- ProgressPill / EpisodeStrip ----------------------------------------------

export function ProgressPill({ progress, label }: { progress: ProjectProgress; label: string }) {
  const tone = PROGRESS_TONE[progress];
  const pulsing = progress === "in_progress";
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border border-border/50 bg-card/60 px-2 py-[2px] font-mono text-[10px] font-semibold uppercase tracking-[0.06em]"
      style={{ color: tone.text }}
    >
      <span
        aria-hidden
        className={pulsing ? "motion-safe:animate-pulse" : undefined}
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
  if (i < summary.completed) return { bg: "var(--good)" };
  if (i < inProductionEnd) {
    return { bg: "var(--primary)", glow: "0 0 6px color-mix(in oklab, var(--primary) 35%, transparent)" };
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
            className="h-[3px] flex-1 rounded-xs"
            style={{ background: c.bg, boxShadow: c.glow }}
          />
        );
      })}
    </div>
  );
}

// -- 渐变进度条 — 复用 ui/ProgressBar，仅注入 Darkroom 视觉 ------------------

export function gradientProgressStyles(variant: "accent" | "good"): {
  trackStyle: CSSProperties;
  barStyle: CSSProperties;
} {
  const trackStyle: CSSProperties = { background: "oklch(0.16 0.010 265)" };
  if (variant === "good") {
    return {
      trackStyle,
      barStyle: {
        background: "linear-gradient(90deg, var(--good), oklch(0.86 0.08 155))",
        boxShadow: "0 0 6px var(--good)",
      },
    };
  }
  return {
    trackStyle,
    barStyle: {
      background: "var(--primary)",
      boxShadow: "0 0 6px color-mix(in oklab, var(--primary) 35%, transparent)",
    },
  };
}

// -- ProjectCard --------------------------------------------------------------

const EMPTY_COUNT = { total: 0, available: 0, stale: 0 } as const;

/** 一类资产的可用计数：产物清单里 current ∪ stale 的那些。 */
export function assetCount(status: ProjectStatus | null, assetType: string): ArtifactCount {
  return status?.assets?.[assetType] ?? EMPTY_COUNT;
}

interface ProjectCardBaseProps {
  project: ProjectSummary;
  styleLabel: string;
}

/**
 * 两种形态互斥：大厅里的真实卡片必须给 `onDelete`，只读演示卡不接受它。两种形态都点得进
 * 工作台——演示项目进的是只读工作台，删除则只对真实项目成立。
 */
type ProjectCardProps = ProjectCardBaseProps &
  ({ readOnly: true; onDelete?: never } | { readOnly?: false; onDelete: () => void });

export function ProjectCard(props: ProjectCardProps) {
  const { project, styleLabel } = props;
  const { t } = useTranslation(["dashboard", "onboarding"]);
  const progressLabel = useProgressLabel();
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
  const progress = projectProgress(status);
  const progressText = progressLabel(status);
  const characters = assetCount(status, "character");
  const scenes = assetCount(status, "scene");
  const propsStat = assetCount(status, "prop");
  const episodes =
    status?.episodes_summary ?? { total: 0, scripted: 0, in_production: 0, completed: 0 };
  const projectDisplayName = getProjectDisplayName(project.title, t("untitled_project"));
  // 演示卡的可读名里带上「只读」：视觉上有 eyebrow 说明，只听朗读的人否则会以为点进的是自己的项目
  // 「待修复」与原因也进可读名：视觉上是一枚 pill 加一行原因，只听朗读的人否则拿不到
  // 这张卡为什么被阻断
  const repairReason = repairReasonOf(status);
  const linkLabel = [
    projectDisplayName,
    styleLabel,
    progressText,
    status?.needs_repair ? t("lobby_card_needs_repair") : "",
    repairReason ?? "",
    props.readOnly ? t("onboarding:demo_banner_title") : "",
  ]
    .filter(Boolean)
    .join(" · ");

  const body = (
    <>
      <div className="p-2.5">
        <Poster project={project} styleLabel={styleLabel} />
      </div>

      <div className="px-4 pt-1 pb-3.5">
        <div className="mb-1.5 flex items-baseline justify-between gap-2">
          <h3 className="truncate text-[17px] font-semibold tracking-tight text-foreground">
            {projectDisplayName}
          </h3>
          <span
            className="shrink-0 font-mono text-[9.5px] uppercase tracking-[0.08em] text-muted-foreground"
            title={styleLabel}
          >
            {styleLabel}
          </span>
        </div>

        <div className="mb-3 flex items-center gap-2">
          <ProgressPill progress={progress} label={progressText} />
          {status?.needs_repair ? <NeedsRepairPill /> : null}
        </div>

        <RepairReasonLine reason={repairReason} />

        <EpisodeStrip summary={episodes} />

        <div
          className="mt-3 grid grid-cols-4 overflow-hidden rounded-md border border-border/50"
          style={{ background: "oklch(0.16 0.010 265 / 0.5)" }}
        >
          {(
            [
              { k: t("lobby_card_stat_cast"), v: characters.available, total: characters.total },
              { k: t("lobby_card_stat_scene"), v: scenes.available, total: scenes.total },
              { k: t("lobby_card_stat_prop"), v: propsStat.available, total: propsStat.total },
              { k: t("lobby_card_stat_episode"), v: episodes.completed, total: episodes.total },
            ] as const
          ).map((cell, i) => (
            <div
              key={cell.k}
              className={
                "px-1.5 py-2 text-center" +
                (i < 3 ? " border-r border-border/50" : "")
              }
            >
              <div className="font-mono text-[8.5px] font-bold tracking-[0.08em] text-muted-foreground">
                {cell.k}
              </div>
              <div className="mt-0.5 font-mono text-[11.5px] font-semibold tabular-nums text-subtle-foreground">
                {cell.v}
                <span className="text-muted-foreground">/{cell.total || "—"}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );

  return (
    <article className="group relative overflow-hidden rounded-xl border border-border bg-card/85 transition-[transform,border-color,box-shadow] duration-150 motion-safe:hover:-translate-y-0.5 hover:border-primary/45 hover:shadow-[0_18px_40px_-22px_oklch(0_0_0_/_0.6),0_0_0_1px_color-mix(in_oklab,var(--primary)_22%,transparent)] focus-within:border-primary/60 focus-within:shadow-[0_0_0_2px_color-mix(in_oklab,var(--primary)_22%,transparent)]">
      <Link
        href={`/app/projects/${project.name}`}
        className="block w-full text-left text-foreground no-underline outline-none"
        aria-label={linkLabel}
      >
        {body}
      </Link>

      {props.readOnly ? null : (
        <div className="absolute right-2.5 bottom-2.5 z-[2]">
          <button
            ref={triggerRef}
            type="button"
            aria-label={`${t("lobby_card_actions")} — ${projectDisplayName}`}
            aria-expanded={menuOpen}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setMenuOpen((v) => !v);
            }}
            className={
              "grid h-8 w-8 place-items-center rounded-md border border-border/50 bg-background/70 text-muted-foreground backdrop-blur transition-[opacity,color,background] hover:bg-background hover:text-subtle-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
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
              className="absolute right-0 bottom-[calc(100%+6px)] min-w-[148px] overflow-hidden rounded-md border border-border bg-card/95 shadow-[0_18px_40px_-22px_oklch(0_0_0_/_0.7)] backdrop-blur"
            >
              <button
                type="button"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setMenuOpen(false);
                  props.onDelete();
                }}
                aria-label={`${t("delete_project")} — ${projectDisplayName}`}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12.5px] text-destructive transition-colors hover:bg-destructive/10 focus-visible:bg-destructive/10 focus-visible:outline-none"
              >
                <Trash2 className="h-3.5 w-3.5" />
                {t("delete_project")}
              </button>
            </div>
          ) : null}
        </div>
      )}
    </article>
  );
}
