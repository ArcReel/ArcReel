/**
 * 项目大厅的卡片及其零件（海报 / 阶段徽标 / 分集条 / 进度条配色）。
 *
 * 从 ProjectsPage 拆出来，是因为引导的演示卡也要用同一张卡 —— 「项目推进后长这样」
 * 这句话只有在演示卡与真实卡片是同一份实现时才不会随时间说谎。演示卡走 `readOnly`
 * 形态：点进去是只读工作台，卡上不带操作菜单。
 */

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Link } from "wouter";
import { MoreHorizontal, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { getProjectDisplayName } from "@/utils/project-display";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { hashHue, posterGridStyle } from "@/components/ui/darkroom-tokens";
import type { ArtifactCount, Phase, ProjectStatus, ProjectSummary } from "@/types";

interface PhaseTone {
  dot: string;
  text: string;
  glow: string;
}

const PHASE_TONE: Record<Phase, PhaseTone> = {
  preparation: {
    dot: "oklch(0.64 0.020 265)",
    text: "oklch(0.78 0.010 265)",
    glow: "transparent",
  },
  script: {
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

const POSTER_GRID_STYLE = posterGridStyle();

const POSTER_SPROCKET_STYLE: CSSProperties = {
  background:
    "repeating-linear-gradient(0deg, oklch(0 0 0 / 0.6) 0 6px, transparent 6px 12px)",
};

export function asProjectStatus(s: ProjectSummary["status"]): ProjectStatus | null {
  return s && "phase" in s ? (s as ProjectStatus) : null;
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
          {getProjectDisplayName(project.title, t("untitled_project"))}
        </div>
      </div>
    </div>
  );
}

// -- 需要修复标记 --------------------------------------------------------------

/** 迁移未跑完的项目在列表上的标记。大厅两张卡（常规卡与「正在编辑」卡）共用一份。 */
export function NeedsRepairPill() {
  const { t } = useTranslation("dashboard");
  return (
    <span
      className="inline-flex items-center rounded-full border px-2 py-[2px] font-mono text-[10px] font-semibold uppercase tracking-[0.06em]"
      style={{
        color: "var(--color-warm)",
        borderColor: "var(--color-warm-ring)",
        background: "var(--color-warm-soft)",
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
    <p className="mb-3 line-clamp-2 break-words font-mono text-[10.5px] leading-[1.45] text-text-3">
      {reason}
    </p>
  );
}

/** 只有确实被阻断的项目才有原因可显示——健康项目上的残留原因不展示。 */
export function repairReasonOf(status: ProjectStatus | null): string | null {
  return status?.needs_repair ? (status.repair_reason ?? null) : null;
}

// -- PhasePill / EpisodeStrip -------------------------------------------------

export function PhasePill({ phase, label }: { phase: Phase | null; label: string }) {
  const tone = phase ? PHASE_TONE[phase] : PHASE_TONE.preparation;
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

export function gradientProgressStyles(variant: "accent" | "good"): {
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

/** 四个阶段的本地化标签。卡片、筛选胶囊、搜索匹配都读同一份。 */
export function usePhaseLabels(): Record<Phase, string> {
  const { t } = useTranslation("dashboard");
  return useMemo(
    () => ({
      preparation: t("phase_preparation"),
      script: t("phase_script"),
      production: t("phase_production"),
      completed: t("phase_completed"),
    }),
    [t],
  );
}

/**
 * 「比当前内容旧」的一行提示。stale 的产物仍然可用，所以它不进缺口计数，
 * 而是单独说明有几件可以考虑重生——大厅卡与「正在编辑」卡共用同一句话。
 */
export function StaleAssetsLine({ count }: { count: number }) {
  const { t } = useTranslation("dashboard");
  if (count <= 0) return null;
  return (
    <div className="mt-2.5 flex items-center gap-1.5 border-t border-dashed border-hairline-soft pt-2.5">
      <span
        aria-hidden
        className="h-[5px] w-[5px] rounded-full"
        style={{ background: "var(--color-warm-bright)" }}
      />
      <span className="font-mono text-[10px] tracking-[0.04em] text-warm-bright">
        {t("lobby_card_stale_assets", { count })}
      </span>
    </div>
  );
}

const EMPTY_COUNT = { total: 0, available: 0, stale: 0 } as const;

/** 一类资产的可用计数：产物清单里 current ∪ stale 的那些。 */
export function assetCount(status: ProjectStatus | null, assetType: string): ArtifactCount {
  return status?.assets?.[assetType] ?? EMPTY_COUNT;
}

/** 全部资产类型的 stale 张数之和——卡片上只列举三类计数，这一行不漏掉其余类型。 */
export function staleAssetTotal(status: ProjectStatus | null): number {
  return Object.values(status?.assets ?? {}).reduce((sum, count) => sum + count.stale, 0);
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
  const phaseLabels = usePhaseLabels();
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
  const phase: Phase | null = status?.phase ?? null;
  const phaseLabel = phase ? phaseLabels[phase] : "";
  const progressPct = status ? Math.round(status.phase_progress * 100) : 0;
  const characters = assetCount(status, "character");
  const scenes = assetCount(status, "scene");
  const propsStat = assetCount(status, "prop");
  const staleAssets = staleAssetTotal(status);
  const episodes =
    status?.episodes_summary ?? { total: 0, scripted: 0, in_production: 0, completed: 0 };
  const projectDisplayName = getProjectDisplayName(project.title, t("untitled_project"));
  // 演示卡的可读名里带上「只读」：视觉上有 eyebrow 说明，只听朗读的人否则会以为点进的是自己的项目
  // 「需要修复」与原因也进可读名：视觉上是一枚 pill 加一行原因，只听朗读的人否则拿不到
  // 这张卡为什么被阻断
  const repairReason = repairReasonOf(status);
  const linkLabel = [
    projectDisplayName,
    styleLabel,
    phaseLabel,
    status?.needs_repair ? t("lobby_card_needs_repair") : "",
    repairReason ?? "",
    staleAssets > 0 ? t("lobby_card_stale_assets", { count: staleAssets }) : "",
    props.readOnly ? t("onboarding:demo_banner_title") : "",
  ]
    .filter(Boolean)
    .join(" · ");

  const { trackStyle, barStyle } = gradientProgressStyles(
    phase === "completed" ? "good" : "accent",
  );

  const body = (
    <>
      <div className="p-2.5">
        <Poster project={project} styleLabel={styleLabel} />
      </div>

      <div className="px-4 pt-1 pb-3.5">
        <div className="mb-1.5 flex items-baseline justify-between gap-2">
          <h3 className="truncate text-[17px] font-semibold tracking-tight text-text">
            {projectDisplayName}
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
          {status?.needs_repair ? <NeedsRepairPill /> : null}
        </div>

        <RepairReasonLine reason={repairReason} />

        <EpisodeStrip summary={episodes} />

        <div
          className="mt-3 grid grid-cols-4 overflow-hidden rounded-[7px] border border-hairline-soft"
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
                (i < 3 ? " border-r border-hairline-soft" : "")
              }
            >
              <div className="font-mono text-[8.5px] font-bold tracking-[0.08em] text-text-3">
                {cell.k}
              </div>
              <div className="mt-0.5 font-mono text-[11.5px] font-semibold tabular-nums text-text-2">
                {cell.v}
                <span className="text-text-4">/{cell.total || "—"}</span>
              </div>
            </div>
          ))}
        </div>

        <div className="mt-3 flex items-center gap-2.5">
          <ProgressBar
            value={progressPct}
            label={t("lobby_now_editing_progress_label")}
            className="h-[3px] rounded-[2px] bg-transparent"
            style={trackStyle}
            barClassName="rounded-none"
            barStyle={barStyle}
          />
          <span
            className="font-mono text-[10.5px] font-semibold tabular-nums"
            style={{
              color: phase === "completed" ? "var(--color-good)" : "var(--color-accent-2)",
            }}
          >
            {progressPct}%
          </span>
        </div>

        <StaleAssetsLine count={staleAssets} />
      </div>
    </>
  );

  return (
    <article className="group relative overflow-hidden rounded-[12px] border border-hairline bg-bg-grad-a/85 transition-[transform,border-color,box-shadow] duration-150 motion-safe:hover:-translate-y-0.5 hover:border-accent/45 hover:shadow-[0_18px_40px_-22px_oklch(0_0_0_/_0.6),0_0_0_1px_var(--color-accent-soft)] focus-within:border-accent/60 focus-within:shadow-[0_0_0_2px_var(--color-accent-soft)]">
      <Link
        href={`/app/projects/${project.name}`}
        className="block w-full text-left text-text no-underline outline-none"
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
                  props.onDelete();
                }}
                aria-label={`${t("delete_project")} — ${projectDisplayName}`}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12.5px] text-danger-2 transition-colors hover:bg-danger-soft focus-visible:bg-danger-soft focus-visible:outline-none"
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
