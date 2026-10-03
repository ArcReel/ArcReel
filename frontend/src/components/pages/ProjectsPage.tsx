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
import { formatDate } from "@/utils/date-format";
import { Link, useLocation } from "wouter";
import { AlertTriangle, Bot, Library, Loader2, Plus, Search, Settings, Upload } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { API } from "@/api";
import { settingsSectionPath } from "@/app-routes";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { ArchiveDiagnosticsDialog } from "@/components/shared/ArchiveDiagnosticsDialog";
import { ConfirmDialog } from "@/components/legacy/ConfirmDialog";
import { GlassModal } from "@/components/legacy/GlassModal";
import { ProgressBar } from "@/components/shared/ProgressBar";
import { SecondaryButton } from "@/components/legacy/SecondaryButton";
import { Typewriter, type TypewriterSegment } from "@/components/pages/Typewriter";
import { WARM_TONE } from "@/utils/severity-tone";
import { getProjectDisplayName } from "@/utils/project-display";
import { CreateProjectModal } from "./CreateProjectModal";
import { ACCENT_BUTTON_STYLE, ICON_BTN_FILLED_CLS } from "@/components/shared/darkroom-tokens";
import {
  ProjectCard,
  Poster,
  ProgressPill,
  NeedsRepairPill,
  RepairReasonLine,
  asProjectStatus,
  assetCount,
  gradientProgressStyles,
  projectProgress,
  repairReasonOf,
  useProgressLabel,
} from "./ProjectCard";
import { ONBOARDING_ANCHORS } from "@/onboarding/anchors";
import { OnboardingDemoCard } from "@/onboarding/OnboardingDemoCard";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { BRAND } from "@/branding";
import {
  type ImportConflictPolicy,
  type ProjectStatus,
  type ImportFailureDiagnostics,
  type ProjectSummary,
} from "@/types";

// 项目大厅 · Darkroom
// 设计：导演的暗房（Claude Design 交付包 ArcReel Projects B Darkroom.html）
// 数据：仅消费 ProjectSummary 真实字段；hue 由 project.name 哈希派生

/**
 * 大厅的筛选维度：按集进度分「进行中 / 已完成」，另有与之正交的「待修复」
 * （项目结构升级失败）。项目没有流水线阶段。
 */
type LobbyFilter = "all" | "in_progress" | "completed" | "repair";
const LOBBY_FILTERS = ["in_progress", "completed", "repair"] as const satisfies readonly LobbyFilter[];

function matchesFilter(status: ProjectStatus | null, filter: LobbyFilter): boolean {
  if (filter === "all") return true;
  if (!status) return false;
  if (filter === "repair") return status.needs_repair;
  const completed = projectProgress(status) === "completed";
  return filter === "completed" ? completed : !completed;
}
type GreetingKey =
  | "lobby_hero_greeting_morning"
  | "lobby_hero_greeting_afternoon"
  | "lobby_hero_greeting_evening"
  | "lobby_hero_greeting_late";


/**
 * 「接着上一次」卡的候选分：已有集进入制作的项目优先，其次是只有脚本的项目；
 * 没有集或已完成的项目不作候选。
 */
function projectActivityScore(p: ProjectSummary): number {
  const status = asProjectStatus(p.status);
  if (!status) return -1;
  const { total, scripted, in_production, completed } = status.episodes_summary;
  if (total === 0) return 0;
  if (projectProgress(status) === "completed") return -10;
  if (in_production + completed > 0) return 100 + (completed / total) * 10;
  return 10 + scripted / total;
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

// -- NowEditingCard -----------------------------------------------------------

interface NowEditingCardProps {
  project: ProjectSummary;
  styleLabel: string;
  t: TFunction;
}

function NowEditingCard({ project, styleLabel, t }: NowEditingCardProps) {
  const status = asProjectStatus(project.status);
  const progress = projectProgress(status);
  const progressText = useProgressLabel()(status);
  const episodes =
    status?.episodes_summary ?? { total: 0, scripted: 0, in_production: 0, completed: 0 };
  const progressPct = episodes.total ? Math.round((episodes.completed / episodes.total) * 100) : 0;
  const characters = assetCount(status, "character");
  const scenes = assetCount(status, "scene");
  const propsStat = assetCount(status, "prop");
  const repairReason = repairReasonOf(status);

  const { trackStyle, barStyle } = gradientProgressStyles(
    progress === "completed" ? "good" : "accent",
  );

  return (
    <article
      className="grid overflow-hidden rounded-xl border border-border bg-card"
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
          <span className="inline-flex items-center gap-1.5 font-mono text-[10px] font-bold tracking-[0.14em] text-primary">
            <span
              aria-hidden
              className="motion-safe:animate-pulse"
              style={{
                width: 5,
                height: 5,
                borderRadius: 3,
                background: "var(--primary)",
                boxShadow: "0 0 8px color-mix(in oklab, var(--primary) 35%, transparent)",
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
            color: "var(--foreground)",
          }}
        >
          {getProjectDisplayName(project.title, t("dashboard:untitled_project"))}
        </h3>
        <div className="font-editorial relative italic text-muted-foreground" style={{ fontSize: 15 }}>
          {styleLabel}
        </div>

        <div aria-hidden className="relative my-4 h-px bg-border/50" />

        <RepairReasonLine reason={repairReason} />

        <div className="relative mb-3 flex items-center gap-3.5">
          <ProgressPill progress={progress} label={progressText} />
          {status?.needs_repair ? <NeedsRepairPill /> : null}
          <div className="flex flex-1 items-center gap-2.5">
            <ProgressBar
              value={progressPct}
              label={t("dashboard:lobby_now_editing_progress_label")}
              className="h-[3px] rounded-xs bg-transparent"
              style={trackStyle}
              barClassName="rounded-none"
              barStyle={barStyle}
            />
            <span className="font-mono text-[11px] font-semibold tabular-nums text-primary">
              {progressPct}%
            </span>
          </div>
        </div>

        <div
          className="relative grid overflow-hidden rounded-md"
          style={{
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: 1,
            background: "color-mix(in oklab, var(--border) 50%, transparent)",
          }}
        >
          {[
            {
              k: t("dashboard:lobby_now_editing_episodes_label"),
              v: t("dashboard:lobby_now_editing_episodes_value", {
                completed: episodes.completed,
                total: episodes.total,
              }),
              sub: progressText,
            },
            {
              k: t("dashboard:characters"),
              v: `${characters.available} / ${characters.total || "—"}`,
              sub: `${t("dashboard:scenes")} ${scenes.available}/${scenes.total || "—"}`,
            },
            {
              k: t("dashboard:props"),
              v: `${propsStat.available} / ${propsStat.total || "—"}`,
              sub: `${t("dashboard:lobby_now_editing_progress_label")} ${progressPct}%`,
            },
          ].map((cell) => (
            <div
              key={cell.k}
              className="px-3.5 py-3"
              style={{ background: "oklch(0.16 0.010 265 / 0.6)" }}
            >
              <div className="font-mono text-[9px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
                {cell.k}
              </div>
              <div className="mt-1 text-[14px] font-semibold tracking-tight text-foreground">
                {cell.v}
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">{cell.sub}</div>
            </div>
          ))}
        </div>

        <div className="flex-1" />
        <div className="relative mt-4 flex justify-end">
          <Link
            href={`/app/projects/${project.name}`}
            className="inline-flex items-center gap-2 rounded-md px-4 py-2.5 text-[12px] font-semibold no-underline transition-transform motion-safe:hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            style={ACCENT_BUTTON_STYLE}
          >
            {t("dashboard:lobby_open_workspace")}
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
      className="group relative flex h-full min-h-[380px] flex-col overflow-hidden rounded-xl border border-dashed border-input bg-card/55 text-left transition-colors hover:border-primary/55 hover:bg-card/75 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      aria-label={ariaLabel ?? title}
    >
      <div className="p-2.5">
        <div
          className="relative grid place-items-center overflow-hidden rounded-sm border border-dashed border-border"
          style={{
            aspectRatio: "2 / 1",
            background:
              "radial-gradient(120% 80% at 30% 30%, oklch(0.26 0.04 290 / 0.5) 0%, transparent 60%), oklch(0.18 0.011 265 / 0.55)",
          }}
        >
          <div className="flex flex-col items-center gap-2.5 transition-transform motion-safe:group-hover:-translate-y-0.5">
            <span
              aria-hidden
              className="grid h-12 w-12 place-items-center rounded-xl"
              style={{
                background:
                  "linear-gradient(180deg, oklch(0.30 0.04 290), oklch(0.22 0.02 280))",
                border: "1px solid oklch(0.76 0.09 295 / 0.4)",
                boxShadow:
                  "inset 0 1px 0 oklch(1 0 0 / 0.06), 0 8px 22px -14px var(--primary)",
                color: "var(--primary)",
              }}
            >
              {icon}
            </span>
            <div className="text-center">
              <div className="text-[15px] font-semibold tracking-tight text-subtle-foreground transition-colors group-hover:text-foreground">
                {title}
              </div>
              <div className="mt-0.5 font-mono text-[9.5px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                {kicker}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div aria-hidden className="space-y-3 px-4 pt-1 pb-3.5">
        <div className="flex items-center justify-between gap-2">
          <span className="block h-3 w-1/2 rounded-xs bg-border/85" />
          <span className="block h-2 w-12 rounded-xs bg-border/65" />
        </div>
        <span className="inline-block h-[18px] w-16 rounded-full border border-dashed border-border" />
        <div className="flex gap-[3px]">
          {Array.from({ length: 8 }).map((_, i) => (
            <span key={i} className="h-[3px] flex-1 rounded-xs bg-border/65" />
          ))}
        </div>
        <div
          className="grid grid-cols-4 overflow-hidden rounded-md border border-dashed border-border"
          style={{ background: "oklch(0.16 0.010 265 / 0.45)" }}
        >
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className={"px-1.5 py-2.5" + (i < 3 ? " border-r border-dashed border-border" : "")}
            >
              <span className="mx-auto block h-1.5 w-8 rounded-xs bg-border/75" />
              <span className="mx-auto mt-1.5 block h-2 w-6 rounded-xs bg-border/55" />
            </div>
          ))}
        </div>
        <div className="flex items-center gap-2.5">
          <span className="h-[3px] flex-1 rounded-xs bg-border/55" />
          <span className="h-2 w-7 rounded-xs bg-border/70" />
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
      kicker="START · NEW REEL"
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
            src="/logo.svg"
            alt={BRAND.name}
            className="h-8 w-8"
          />
          <span
            className="font-sans text-[17px] font-medium tracking-[-0.012em] text-foreground"
            aria-hidden
          >
            {BRAND.name}
          </span>
        </div>

        <label className="ml-2 flex w-[min(420px,100%)] items-center gap-2 rounded-lg border border-border/50 bg-background/55 px-3 py-1.5 transition-colors focus-within:border-primary/60">
            <Search className="h-3.5 w-3.5 text-muted-foreground" />
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
              className="flex-1 bg-transparent text-[12.5px] text-foreground placeholder:text-muted-foreground outline-none"
            />
            <kbd
              aria-hidden
              className="rounded-sm border border-border/50 px-1.5 py-px font-mono text-[9.5px] text-muted-foreground"
            >
              {t("dashboard:lobby_search_kbd")}
            </kbd>
        </label>

        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={onAssets}
            className="inline-flex items-center gap-1.5 rounded-md border border-primary/25 bg-primary/12 px-3 py-1.5 text-[12px] text-subtle-foreground transition-colors hover:border-primary/50 hover:bg-primary/22 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            title={t("assets:library_title")}
          >
            <Library className="h-3.5 w-3.5" />
            {t("assets:library_title")}
          </button>
          <span aria-hidden className="mx-1 h-5 w-px bg-border/50" />
          <button
            type="button"
            onClick={onImport}
            disabled={importing}
            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card/50 px-3 py-1.5 text-[12px] text-subtle-foreground transition-colors hover:border-input hover:bg-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
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
            data-onboarding={ONBOARDING_ANCHORS.lobbyCreateProject}
            className="inline-flex items-center gap-1.5 rounded-md px-3.5 py-1.5 text-[12px] font-semibold transition-transform motion-safe:hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            style={ACCENT_BUTTON_STYLE}
          >
            <Plus className="h-3.5 w-3.5" />
            {t("dashboard:create_project")}
          </button>
          <span aria-hidden className="mx-1 h-5 w-px bg-border/50" />
          <Link
            href={settingsSectionPath("external-agent")}
            className="rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-card hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            title={t("dashboard:settings_external_agent")}
            aria-label={t("dashboard:settings_external_agent")}
          >
            <Bot className="h-4 w-4" aria-hidden />
          </Link>
          <button
            type="button"
            onClick={onSettings}
            data-onboarding={ONBOARDING_ANCHORS.lobbySettings}
            className={`relative ${ICON_BTN_FILLED_CLS}`}
            title={t("settings")}
            aria-label={t("settings")}
          >
            <Settings className="h-4 w-4" aria-hidden />
            {configIncomplete ? (
              <span
                aria-label={t("config_incomplete")}
                className="absolute right-0.5 top-0.5 h-2 w-2 rounded-full bg-warn"
              />
            ) : null}
          </button>
        </div>
      </div>
    </div>
  );
}

// -- HeroStrip ----------------------------------------------------------------

const KICKER_DATE_OPTS: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  weekday: "short",
};

interface HeroStripProps {
  totals: {
    total: number;
    inProgress: number;
    completed: number;
    repair: number;
    episodesCompleted: number;
    episodesInProduction: number;
  };
  t: TFunction;
}

function HeroStrip({ totals, t }: HeroStripProps) {
  const { i18n } = useTranslation();
  const greetingKey = useMemo<GreetingKey>(() => getGreetingKey(), []);
  const dateLine = useMemo(
    () => formatDate(new Date(), i18n.language || "zh", KICKER_DATE_OPTS, new Date().toISOString().slice(0, 10)),
    [i18n.language],
  );

  let subtitle: string;
  if (totals.inProgress > 0) {
    subtitle = t("dashboard:lobby_hero_subtitle_active", { count: totals.inProgress });
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
      tone: { color: "var(--foreground)" },
    },
    {
      key: "in_progress",
      label: t("dashboard:lobby_filter_in_progress"),
      value: totals.inProgress,
      tone: { color: "var(--primary)" },
    },
    {
      key: "completed",
      label: t("dashboard:lobby_filter_completed"),
      value: totals.completed,
      tone: { color: "var(--good)" },
    },
    {
      key: "repair",
      label: t("dashboard:lobby_filter_repair"),
      value: totals.repair,
      tone: { color: "var(--warn)" },
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
            color: "var(--foreground)",
          }}
        >
          <Typewriter
            once="lobby-hero"
            segments={
              [
                { text: t(`dashboard:${greetingKey}`), after: <br /> },
                {
                  text: subtitle,
                  style: { fontStyle: "italic", color: "var(--primary)" },
                },
              ] satisfies TypewriterSegment[]
            }
          />
        </h1>
        <p className="m-0 mt-2.5 max-w-[560px] text-[13px] leading-[1.55] text-muted-foreground">
          {summaryLine}
        </p>
      </div>
      <div className="flex flex-col items-end justify-between gap-2.5">
        <div className="mt-1.5 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-primary">
          {t("dashboard:lobby_hero_eyebrow")} — {dateLine}
        </div>
        <div
          data-testid="lobby-hero-stats"
          className="flex items-stretch overflow-hidden rounded-lg border border-border/50"
          style={{ background: "oklch(0.16 0.010 265 / 0.4)" }}
        >
          {stats.map((s, i) => (
            <div
              key={s.key}
              className={
                "px-4 py-2.5" +
                (i < stats.length - 1 ? " border-r border-border/50" : "")
              }
            >
              <div className="font-mono text-[9px] font-bold whitespace-nowrap uppercase tracking-[0.14em] text-muted-foreground">
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
  active: LobbyFilter;
  onChange: (next: LobbyFilter) => void;
  counts: Record<LobbyFilter, number>;
  t: TFunction;
}

function FilterPills({ active, onChange, counts, t }: FilterPillsProps) {
  const pills: Array<{ key: LobbyFilter; label: string; n: number }> = [
    { key: "all", label: t("dashboard:lobby_filter_all"), n: counts.all },
    ...LOBBY_FILTERS.map((filter) => ({
      key: filter,
      label: t(`dashboard:lobby_filter_${filter}`),
      n: counts[filter],
    })),
  ];

  return (
    <div
      className="sticky z-20 border-b border-border backdrop-blur-md"
      style={{
        top: "var(--lobby-topbar-h, 57px)",
        background:
          "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.55), oklch(0.15 0.010 265 / 0.45))",
        backdropFilter: "blur(16px) saturate(1.1)",
        borderTopWidth: 1,
        borderTopColor: "color-mix(in oklab, var(--border) 50%, transparent)",
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
                "inline-flex items-center rounded-full px-3 py-1 text-[11.5px] font-medium backdrop-blur-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
                (isActive
                  ? "border border-primary/40 bg-primary/45 text-foreground"
                  : "border border-border/50 bg-[oklch(0.22_0.012_265_/_0.7)] text-muted-foreground hover:border-border hover:bg-[oklch(0.24_0.012_265_/_0.78)] hover:text-subtle-foreground")
              }
            >
              {c.label}
              <span
                className={
                  "ml-1.5 font-mono tabular-nums " +
                  (isActive ? "text-primary" : "text-muted-foreground")
                }
              >
                {c.n}
              </span>
            </button>
          );
        })}
        <div className="flex-1" />
        <span className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-muted-foreground">
          {t("dashboard:lobby_sort_recent")}
        </span>
      </div>
    </div>
  );
}

// -- ProjectsPage -------------------------------------------------------------

export function ProjectsPage() {
  const { t } = useTranslation(["common", "dashboard", "assets"]);
  const [, navigate] = useLocation();
  const {
    projects,
    projectsLoading,
    showCreateModal,
    setProjects,
    setProjectsLoading,
    setShowCreateModal,
  } = useProjectsStore();
  const tourActive = useOnboardingStore((s) => s.active);

  const [importingProject, setImportingProject] = useState(false);
  const [conflictProject, setConflictProject] = useState<string | null>(null);
  const [conflictFile, setConflictFile] = useState<File | null>(null);
  type ImportDiagnosticsState =
    | { source: "success"; diagnostics: ImportFailureDiagnostics; navigateTo: string }
    | { source: "failure"; diagnostics: ImportFailureDiagnostics };
  const [importDiagnostics, setImportDiagnostics] =
    useState<ImportDiagnosticsState | null>(null);
  const [deletingProject, setDeletingProject] = useState<ProjectSummary | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [lobbyFilter, setLobbyFilter] = useState<LobbyFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const importInputRef = useRef<HTMLInputElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const isConfigComplete = useConfigStatusStore((s) => s.isComplete);

  const progressLabel = useProgressLabel();

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
                  title: getProjectDisplayName(
                    result.project.title,
                    t("dashboard:untitled_project"),
                  ),
                  count: autoFixedCount,
                })
              : t("dashboard:import_success", {
                  title: getProjectDisplayName(
                    result.project.title,
                    t("dashboard:untitled_project"),
                  ),
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
          t("dashboard:delete_project_failed", { title: projectDisplayName, message: errMsg(err) }),
          "warning",
        );
    } finally {
      setDeleteLoading(false);
      setDeletingProject(null);
    }
  };

  const filterCounts = useMemo(() => {
    const out: Record<LobbyFilter, number> = { all: 0, in_progress: 0, completed: 0, repair: 0 };
    for (const p of projects) {
      const status = asProjectStatus(p.status);
      for (const filter of ["all", ...LOBBY_FILTERS] as const) {
        if (matchesFilter(status, filter)) out[filter] += 1;
      }
    }
    return out;
  }, [projects]);

  const totals = useMemo(() => {
    // Hero 计数与筛选胶囊读同一套词汇：Hero 报的每一个数都能在下面的胶囊上点开。
    let episodesCompleted = 0;
    let episodesInProduction = 0;
    for (const p of projects) {
      const s = asProjectStatus(p.status);
      if (!s) continue;
      episodesCompleted += s.episodes_summary.completed;
      episodesInProduction += s.episodes_summary.in_production;
    }
    return {
      total: projects.length,
      inProgress: filterCounts.in_progress,
      completed: filterCounts.completed,
      repair: filterCounts.repair,
      episodesCompleted,
      episodesInProduction,
    };
  }, [projects, filterCounts]);

  const styleLabels = useMemo(() => {
    const map: Record<string, string> = {};
    for (const p of projects) map[p.name] = styleLabelOf(p, t);
    return map;
  }, [projects, t]);

  const filteredProjects = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return projects.filter((p) => {
      const s = asProjectStatus(p.status);
      if (!matchesFilter(s, lobbyFilter)) return false;
      if (!q) return true;
      return `${p.title || ""} ${p.name} ${progressLabel(s)}`.toLowerCase().includes(q);
    });
  }, [projects, lobbyFilter, searchQuery, progressLabel]);

  const featuredCandidate = useMemo(() => pickFeaturedProject(projects), [projects]);
  const featured =
    lobbyFilter === "all" && !searchQuery.trim() ? featuredCandidate : null;

  const restProjects = useMemo(
    () =>
      featured
        ? filteredProjects.filter((p) => p.name !== featured.name)
        : filteredProjects,
    [featured, filteredProjects],
  );

  return (
    <div
      className="relative min-h-screen text-foreground"
      style={
        {
          // FilterPills 的 sticky top 读这个变量；TopBar = logo h-8 (32) + py-3 (24) + 1px border
          "--lobby-topbar-h": "57px",
          background:
            "radial-gradient(1100px 540px at 8% -10%, oklch(0.32 0.05 295 / 0.28), transparent 55%), radial-gradient(900px 500px at 100% 110%, oklch(0.26 0.04 260 / 0.25), transparent 55%), linear-gradient(180deg, var(--card), var(--sidebar))",
        } as CSSProperties
      }
    >
      <TopBar
        searchValue={searchQuery}
        onSearch={setSearchQuery}
        onImport={() => importInputRef.current?.click()}
        onCreate={() => setShowCreateModal(true)}
        onSettings={() => navigate("/app/settings")}
        onAssets={() => navigate("/app/assets")}
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
          active={lobbyFilter}
          onChange={setLobbyFilter}
          counts={filterCounts}
          t={t}
        />
      ) : null}

      <main className="mx-auto max-w-[1320px] px-6 pt-6 pb-16">
        {/* 引导运行期间才挂，退出即卸载。放在加载/空态分支之外——首次使用时项目列表通常是空的，
            而演示卡正是那一刻最需要讲的东西。 */}
        {tourActive ? <OnboardingDemoCard /> : null}
        {projectsLoading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-6 w-6 motion-safe:animate-spin text-primary" />
            <span className="ml-2 text-muted-foreground">{t("dashboard:loading_projects")}</span>
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
                    className="m-0 font-mono text-[12.5px] font-semibold uppercase tracking-[0.06em] text-subtle-foreground"
                  >
                    {t("dashboard:lobby_now_editing_eyebrow")}
                  </h2>
                </div>
                <NowEditingCard
                  project={featured}
                  styleLabel={styleLabels[featured.name] ?? ""}
                  t={t}
                />
              </section>
            ) : null}

            {filteredProjects.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <p className="text-lg text-foreground">{t("dashboard:lobby_no_filter_match")}</p>
                <p className="mt-1 text-sm">{t("dashboard:lobby_no_filter_match_hint")}</p>
                <button
                  type="button"
                  onClick={() => {
                    setLobbyFilter("all");
                    setSearchQuery("");
                  }}
                  className="mt-4 rounded-md border border-border px-3 py-1.5 text-[12px] text-subtle-foreground hover:border-primary/40 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {t("dashboard:lobby_clear_filters")}
                </button>
              </div>
            ) : (
              <section aria-labelledby="lobby-library-heading">
                <div className="mb-3 flex items-baseline justify-between">
                  <h2
                    id="lobby-library-heading"
                    className="m-0 font-mono text-[12.5px] font-semibold uppercase tracking-[0.06em] text-subtle-foreground"
                  >
                    {t("dashboard:lobby_library_eyebrow")}
                  </h2>
                  <span className="font-mono text-[10.5px] tabular-nums text-muted-foreground">
                    {t("dashboard:lobby_library_count", { count: restProjects.length })}
                  </span>
                </div>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {restProjects.map((project) => (
                    <ProjectCard
                      key={project.name}
                      project={project}
                      styleLabel={styleLabels[project.name] ?? ""}
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

      {showCreateModal && <CreateProjectModal />}

      <ConfirmDialog
        open={!!deletingProject}
        tone="danger"
        title={t("dashboard:delete_project")}
        description={
          deletingProject
            ? t("dashboard:confirm_delete_project", {
                title: deletingProject.title || deletingProject.name,
              })
            : null
        }
        confirmLabel={t("dashboard:delete_project")}
        loadingLabel={t("dashboard:deleting_project")}
        cancelLabel={t("common:cancel")}
        loading={deleteLoading}
        onCancel={() => {
          if (!deleteLoading) setDeletingProject(null);
        }}
        onConfirm={handleDeleteProject}
      />
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
  return (
    <GlassModal
      open
      onClose={onCancel}
      labelledBy="lobby-conflict-title"
      widthClassName="w-full max-w-lg"
      hairlineTone="warm"
      closeOnBackdrop={!importing}
      closeOnEscape={!importing}
    >
      <div className="px-6 pb-6 pt-5">
        <div className="flex items-start gap-3">
          <span
            aria-hidden
            className="grid h-9 w-9 shrink-0 place-items-center rounded-xl"
            style={{
              background:
                "linear-gradient(135deg, color-mix(in oklab, var(--warn) 15%, transparent), color-mix(in oklab, var(--warn) 5%, transparent))",
              border: `1px solid ${WARM_TONE.ring}`,
              color: WARM_TONE.color,
              boxShadow: `0 8px 18px -8px ${WARM_TONE.glow}`,
            }}
          >
            <AlertTriangle className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1 space-y-1.5">
            <h2
              id="lobby-conflict-title"
              className="display-serif text-[17px] font-semibold tracking-tight"
              style={{ color: "var(--foreground)" }}
            >
              {t("dashboard:duplicate_project_id")}
            </h2>
            <p
              className="text-[12.5px] leading-relaxed"
              style={{ color: "var(--muted-foreground)" }}
            >
              {t("dashboard:id_intended_hint")}
              <span className="mx-1 rounded-sm bg-background/70 px-1.5 py-0.5 font-mono text-foreground">
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
            className="flex w-full items-center justify-between rounded-xl border border-warn/30 bg-warn/15 px-4 py-3 text-left text-sm text-warn transition-colors hover:border-warn/60 hover:bg-warn/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-warn/30 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <span>
              <span className="block font-medium">{t("dashboard:overwrite_existing")}</span>
              <span className="mt-1 block text-xs text-warn/80">
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
            className="flex w-full items-center justify-between rounded-xl border border-primary/25 bg-primary/12 px-4 py-3 text-left text-sm text-foreground transition-colors hover:border-primary/40 hover:bg-primary/22 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
          >
            <span>
              <span className="block font-medium">{t("dashboard:auto_rename_import")}</span>
              <span className="mt-1 block text-xs text-muted-foreground">
                {t("dashboard:rename_hint")}
              </span>
            </span>
            {importing && <Loader2 className="h-4 w-4 motion-safe:animate-spin" />}
          </button>
        </div>

        <div className="mt-5 flex justify-end">
          <SecondaryButton size="sm" onClick={onCancel} disabled={importing}>
            {t("cancel")}
          </SecondaryButton>
        </div>
      </div>
    </GlassModal>
  );
}
