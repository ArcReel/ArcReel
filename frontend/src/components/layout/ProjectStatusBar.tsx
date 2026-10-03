import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";
import { AlertTriangle, Bot, ChevronDown, Loader2 } from "lucide-react";

import { API } from "@/api";
import { EPISODE_PLANNING_SLOTS, enqueueEpisodePlanning } from "@/actions/generation";
import { ApiRequestError } from "@/api/errors";
import { episodesViewPath } from "@/components/canvas/episodes/episodes-view-model";
import { prefillAssistant } from "@/components/shared/DraftStatus";
import { Popover } from "@/components/legacy/FloatingPopover";
import { GHOST_BTN_CLS } from "@/components/shared/darkroom-tokens";
import { StepActButton } from "@/components/workflow/StepActButton";
import type { StepAct } from "@/components/workflow/step-list";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { isResourceBusy, useActiveResourceIds, useTasksStore } from "@/stores/tasks-store";
import type { EpisodeMeta } from "@/types";
import type { EpisodeNextStep, WorkflowStatus } from "@/types/workflow";
import { errMsg } from "@/utils/async";
import { episodeDisplayName } from "@/utils/episode-display";
import { lastInstruction, rememberInstruction } from "@/utils/last-instruction";
import {
  actionPhrase,
  episodeNeedsUpdate,
  projectNextGuide,
  withInstruction,
  type GuideButton,
  type ProjectNextGuide,
} from "./project-guide";

/** 任务状态连续跳变时合并成一次重新求解的窗口（毫秒），与集页面板同一取值。 */
const STATUS_REFRESH_DEBOUNCE_MS = 250;

/** 平的状态面：不内凹、不含凸起亮片，避免读成分段开关。 */
const FLAT = {
  background: "linear-gradient(180deg, oklch(0.25 0.012 265 / 0.9), oklch(0.22 0.011 265 / 0.9))",
  border: "1px solid var(--border)",
  boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.04), 0 1px 2px oklch(0 0 0 / 0.3)",
};
const FLAT_WARM = {
  background: "color-mix(in oklab, var(--warn) 10%, transparent)",
  border: "1px solid color-mix(in oklab, var(--warn) 30%, transparent)",
  boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.04)",
};
const SHELL = "inline-flex h-[28px] items-center overflow-hidden rounded-full";

type Open = "episodes" | "next" | "migration" | null;

/** 当前所在集页的集 ID；不在集页时为 null。 */
function useCurrentEpisodeId(): number | null {
  const [location] = useLocation();
  const match = /^\/episodes\/(\d+)/.exec(location);
  return match ? Number(match[1]) : null;
}

/** 项目层的制作状态：随项目快照与本项目任务的变化重新求解。 */
function useProjectWorkflowStatus(projectName: string, enabled: boolean): WorkflowStatus | null {
  const [status, setStatus] = useState<WorkflowStatus | null>(null);
  const snapshotRevision = useProjectsStore((s) => s.projectSnapshotRevisions[projectName] ?? 0);
  const taskFingerprint = useTasksStore((s) =>
    s.tasks
      .filter((task) => task.project_name === projectName)
      .map((task) => `${task.task_id}:${task.status}`)
      .join("|"),
  );
  const settledTaskFingerprint = useDebouncedValue(taskFingerprint, STATUS_REFRESH_DEBOUNCE_MS);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    API.getWorkflowStatus(projectName, { signal: controller.signal })
      .then(setStatus)
      .catch(() => {
        // 状态求解失败只让右段暂不显示，不打断工作台；下一次快照变化会重试。
        if (!controller.signal.aborted) setStatus(null);
      });
    return () => controller.abort();
  }, [projectName, enabled, snapshotRevision, settledTaskFingerprint]);

  return enabled ? status : null;
}

function Segment({
  children,
  onClick,
  expanded,
  label,
  segmentRef,
}: {
  children: ReactNode;
  onClick?: () => void;
  expanded?: boolean;
  label?: string;
  segmentRef?: React.Ref<HTMLButtonElement>;
}) {
  return (
    <button
      ref={segmentRef}
      type="button"
      onClick={onClick}
      disabled={!onClick}
      aria-expanded={onClick ? expanded : undefined}
      aria-label={label}
      className="focus-ring inline-flex h-full items-center gap-1.5 whitespace-nowrap px-3 text-xs enabled:hover:bg-[oklch(1_0_0_/_0.04)] disabled:cursor-default"
    >
      {children}
    </button>
  );
}

function Divider({ warm }: { warm?: boolean }) {
  return (
    <span
      aria-hidden
      className="h-3.5 w-px"
      style={{ background: warm ? "color-mix(in oklab, var(--warn) 30%, transparent)" : "var(--border)" }}
    />
  );
}

/** 15px 进度环，与数字徽标同尺寸。 */
function Ring({ done, total }: { done: number; total: number }) {
  const r = 6;
  const c = 2 * Math.PI * r;
  const f = total ? Math.min(done / total, 1) : 0;
  return (
    <svg width="15" height="15" viewBox="0 0 15 15" aria-hidden>
      <circle cx="7.5" cy="7.5" r={r} fill="none" stroke="oklch(0.32 0.012 265)" strokeWidth="2.2" />
      <circle
        cx="7.5"
        cy="7.5"
        r={r}
        fill="none"
        stroke="var(--primary)"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeDasharray={`${c * f} ${c}`}
        transform="rotate(-90 7.5 7.5)"
      />
    </svg>
  );
}

function episodeDotColor(episode: EpisodeMeta): string {
  if (episode.status === "completed") return "var(--good)";
  if (episodeNeedsUpdate(episode)) return "var(--warn)";
  if (episode.status === "in_production") return "var(--primary)";
  return "var(--muted-foreground)";
}

function GuideButtonView({
  button,
  primary,
  instruction,
  projectName,
  onNavigate,
}: {
  button: GuideButton;
  primary: boolean;
  instruction: string;
  projectName: string;
  onNavigate: (to: string) => void;
}) {
  const { t } = useTranslation();
  const [submitting, setSubmitting] = useState(false);
  const act: StepAct =
    button.kind === "nav"
      ? { key: button.label, label: button.label, kind: "nav", intent: { type: "route", path: button.to } }
      : button.kind === "agent"
        ? { key: button.label, label: button.label, kind: "agent", intent: { type: "agent", text: button.prefill } }
        : { key: button.label, label: button.label, kind: "ai", intent: { type: "route", path: episodesViewPath() } };
  const planEpisodes = async () => {
    if (EPISODE_PLANNING_SLOTS.some((slot) => isResourceBusy("text_episode_plan", projectName, slot))) {
      useAppStore.getState().pushToast(t("dashboard:episode_planning_busy"), "error");
      return;
    }
    setSubmitting(true);
    try {
      await enqueueEpisodePlanning(projectName, instruction.trim() || null);
      onNavigate(episodesViewPath());
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setSubmitting(false);
    }
  };
  const onRun = () => {
    if (button.kind === "nav") onNavigate(button.to);
    else if (button.kind === "agent") prefillAssistant(withInstruction(t, button.prefill, instruction));
    else void planEpisodes();
  };
  return <StepActButton act={act} onRun={onRun} size={primary ? "md" : "sm"} asLink={!primary} busy={submitting} />;
}

function NextPanel({
  guide,
  projectName,
  onNavigate,
}: {
  guide: ProjectNextGuide;
  projectName: string;
  onNavigate: (to: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  const [instruction, setInstruction] = useState(() => lastInstruction(projectName));
  const updateInstruction = (value: string) => {
    setInstruction(value);
    rememberInstruction(projectName, value);
  };
  return (
    <div className="space-y-2">
      <p className="m-0 text-[12.5px] leading-[1.55]" style={{ color: "var(--subtle-foreground)" }}>
        {guide.detail}
      </p>
      {guide.instruction && (
        <label className="block">
          <span className="mb-0.5 block text-[11px]" style={{ color: "var(--muted-foreground)" }}>
            {t("guide_instruction_label")}
          </span>
          <input
            value={instruction}
            onChange={(e) => updateInstruction(e.target.value)}
            placeholder={t("guide_instruction_placeholder")}
            className="focus-ring w-full rounded-md px-2 py-1 text-[12px]"
            style={{
              background: "var(--muted)",
              border: "1px solid var(--border)",
              color: "var(--foreground)",
            }}
          />
        </label>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {guide.primary.map((button) => (
          <GuideButtonView
            key={button.label}
            button={button}
            primary
            instruction={guide.instruction ? instruction : ""}
            projectName={projectName}
            onNavigate={onNavigate}
          />
        ))}
        {guide.alternatives.length > 0 && (
          <span className="inline-flex flex-wrap items-center gap-1.5 text-[12px]" style={{ color: "var(--muted-foreground)" }}>
            {t("guide_or")}
            {guide.alternatives.map((button) => (
              <GuideButtonView
                key={button.label}
                button={button}
                primary={false}
                instruction=""
                projectName={projectName}
                onNavigate={onNavigate}
              />
            ))}
          </span>
        )}
      </div>
    </div>
  );
}

function EpisodeList({
  projectName,
  episodes,
  current,
  onNavigate,
}: {
  projectName: string;
  episodes: EpisodeMeta[];
  current: number | null;
  onNavigate: (to: string) => void;
}) {
  const { t } = useTranslation();
  const [steps, setSteps] = useState<Map<number, EpisodeNextStep> | null>(null);
  const snapshotRevision = useProjectsStore((s) => s.projectSnapshotRevisions[projectName] ?? 0);

  useEffect(() => {
    const controller = new AbortController();
    API.getEpisodeNextSteps(projectName, { signal: controller.signal })
      .then((res) => setSteps(new Map(res.episodes.map((step) => [step.episode, step]))))
      .catch(() => {
        if (!controller.signal.aborted) setSteps(new Map());
      });
    return () => controller.abort();
  }, [projectName, snapshotRevision]);

  const rowNote = (episode: EpisodeMeta): string => {
    if (episode.status === "completed") return t("dashboard:guide_episode_completed");
    const step = steps?.get(episode.episode);
    if (!step) return steps === null ? "…" : "";
    if (step.plan_stale) return t("dashboard:guide_episode_plan_stale");
    if (step.next_action.type === "none") return t("workflow:action_none");
    return t("dashboard:guide_episode_next", { step: actionPhrase(t, step.next_action.type) });
  };

  return (
    <ol className="m-0 max-h-[360px] list-none space-y-0.5 overflow-y-auto p-0">
      {episodes.map((episode, index) => (
        <li key={episode.episode}>
          <button
            type="button"
            onClick={() => onNavigate(`/episodes/${episode.episode}`)}
            aria-current={current === episode.episode ? "page" : undefined}
            className="focus-ring flex w-full items-center gap-2 rounded-sm px-1.5 py-1 text-left text-[12px] hover:bg-[oklch(1_0_0_/_0.05)]"
            style={current === episode.episode ? { background: "color-mix(in oklab, var(--primary) 12%, transparent)" } : undefined}
          >
            <span aria-hidden className="h-2 w-2 shrink-0 rounded-full" style={{ background: episodeDotColor(episode) }} />
            <span className="w-12 shrink-0 tabular-nums" style={{ color: "var(--muted-foreground)" }}>
              {t("common:episode_position_name", { position: index + 1 })}
            </span>
            <span className="min-w-0 flex-1 truncate" style={{ color: "var(--foreground)" }}>
              {episodeDisplayName(episodes, episode.episode, t)}
            </span>
            <span className="shrink-0 text-[11px]" style={{ color: "var(--muted-foreground)" }}>
              {rowNote(episode)}
            </span>
          </button>
        </li>
      ))}
    </ol>
  );
}

/** 迁移失败：整条变暖色，「重试」直接重跑数据升级链；失败后标题带次数，弹层自动展开原因。 */
function MigrationBar({ projectName, reason }: { projectName: string; reason: string | null }) {
  const { t } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [failures, setFailures] = useState(0);
  const [lastReason, setLastReason] = useState<string | null>(null);
  const anchorRef = useRef<HTMLDivElement>(null);

  const retry = async () => {
    if (running) return;
    setRunning(true);
    try {
      await API.retryProjectMigration(projectName);
      await useProjectsStore.getState().refreshProject(projectName);
      useAppStore.getState().pushToast(t("migration_retry_succeeded"), "success");
    } catch (err) {
      const diagnostic = err instanceof ApiRequestError ? err.diagnostic : null;
      const detail =
        diagnostic && typeof diagnostic === "object" && "reason" in diagnostic
          ? String(diagnostic.reason)
          : err instanceof Error
            ? err.message
            : null;
      setLastReason(detail);
      setFailures((count) => count + 1);
      setOpen(true);
    } finally {
      setRunning(false);
    }
  };

  const shownReason = lastReason ?? reason;
  const title = failures > 0 ? t("migration_retry_failed_title", { count: failures }) : t("migration_bar_title");
  return (
    <div ref={anchorRef} className="relative">
      <div className={SHELL} style={FLAT_WARM}>
        {/* 窄屏只留图标与「重试」，标题收进无障碍名称与弹层。 */}
        <Segment onClick={() => setOpen((value) => !value)} expanded={open} label={title}>
          <AlertTriangle aria-hidden className="h-3.5 w-3.5" style={{ color: "var(--warn)" }} />
          <span aria-hidden className="hidden md:inline" style={{ color: "var(--foreground)" }}>
            {title}
          </span>
          <ChevronDown aria-hidden className="h-3 w-3" style={{ color: "var(--muted-foreground)" }} />
        </Segment>
        <Divider warm />
        <div className="px-1.5">
          <button
            type="button"
            onClick={() => void retry()}
            disabled={running}
            className="focus-ring inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-[11.5px] font-medium disabled:cursor-wait"
            style={{ background: "var(--foreground)", color: "oklch(0.15 0 0)" }}
          >
            {running && <Loader2 aria-hidden className="h-3 w-3 motion-safe:animate-spin" />}
            {running ? t("migration_retry_running") : t("migration_retry")}
          </button>
        </div>
      </div>
      <Popover open={open} onClose={() => setOpen(false)} anchorRef={anchorRef} align="center" width="w-[min(440px,calc(100vw-24px))]">
        <div className="space-y-2 p-3" role="alert">
          <p className="m-0 text-[12px] font-semibold leading-[1.55]" style={{ color: "var(--foreground)" }}>
            {failures > 0 ? t("migration_retry_failed_heading") : t("migration_repair_title")}
          </p>
          <p className="m-0 text-[12px] leading-[1.55]" style={{ color: "var(--subtle-foreground)" }}>
            {failures > 0 ? t("migration_retry_failed_body") : t("migration_repair_body")}
          </p>
          {shownReason ? (
            <p className="m-0 break-words font-mono text-[11.5px] leading-[1.5]" style={{ color: "var(--muted-foreground)" }}>
              {shownReason}
            </p>
          ) : null}
          {failures > 0 && (
            <button
              type="button"
              onClick={() => prefillAssistant(t("migration_repair_prefill"))}
              className={GHOST_BTN_CLS}
            >
              <Bot aria-hidden className="h-3.5 w-3.5" />
              {t("migration_hand_to_agent")}
            </button>
          )}
        </div>
      </Popover>
    </div>
  );
}

/**
 * 顶栏中间的状态条：左段是集进度（点开是逐集清单），右段是项目层的下一步（点开是说明与按钮）。
 * 数据升级失败时整条换成迁移形态。只投影后端给出的项目摘要与制作状态，不自行推断下一步。
 */
export function ProjectStatusBar({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const [, setLocation] = useLocation();
  const demoMode = useDemoWorkbench();
  const project = useProjectsStore((s) => s.currentProjectData);
  const currentEpisode = useCurrentEpisodeId();
  const [open, setOpen] = useState<Open>(null);
  const episodesRef = useRef<HTMLButtonElement>(null);
  const nextRef = useRef<HTMLButtonElement>(null);

  const summary = project?.status;
  const needsRepair = summary?.needs_repair === true;
  const workflowStatus = useProjectWorkflowStatus(projectName, !demoMode && !needsRepair && Boolean(summary));
  const episodes = useMemo(() => project?.episodes ?? [], [project?.episodes]);
  const activePlanning = useActiveResourceIds("text_episode_plan", projectName);
  const planningActive = EPISODE_PLANNING_SLOTS.some((slot) => activePlanning.has(slot));
  const guide = useMemo(
    () => (workflowStatus ? projectNextGuide(t, workflowStatus, episodes, { planningActive }) : null),
    [t, workflowStatus, episodes, planningActive],
  );

  if (!project || !summary) return null;
  if (needsRepair) return <MigrationBar projectName={projectName} reason={summary.repair_reason} />;

  const toggle = (key: Exclude<Open, null>) => setOpen((value) => (value === key ? null : key));
  const navigate = (to: string) => {
    setOpen(null);
    setLocation(to);
  };
  const total = summary.episodes_summary.total;
  const done = summary.episodes_summary.completed;
  const staleEpisodes = episodes.filter(episodeNeedsUpdate).length;
  const isAd = project.content_mode === "ad";
  const progressText =
    total === 0
      ? t("guide_no_episodes")
      : isAd
        ? done >= total
          ? t("guide_ad_completed")
          : t("guide_ad_incomplete")
        : t("guide_progress", { done, total });
  // 集页上：项目层下一步指向本集时让位给本集面板，不出现两个重复的按钮。
  const yieldToPanel = guide !== null && currentEpisode !== null && guide.episodeId === currentEpisode;
  // 每集都完成且源文没有剩余：右段只陈述「全部完成」，不带动作。广告的左段已写「短片已完成」。
  const allComplete = guide === null && !isAd && workflowStatus?.content?.project_complete === true;

  return (
    <div className="relative">
      <div className={SHELL} style={FLAT}>
        <Segment
          segmentRef={episodesRef}
          onClick={total > 0 && !isAd ? () => toggle("episodes") : undefined}
          expanded={open === "episodes"}
        >
          <Ring done={done} total={total} />
          <span className="num" style={{ color: "var(--subtle-foreground)" }}>
            {progressText}
          </span>
          {staleEpisodes > 0 && (
            <span
              className="inline-flex items-center gap-1 text-[11px]"
              style={{ color: "var(--warn)" }}
              title={t("guide_stale_episodes", { count: staleEpisodes })}
            >
              <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--warn)" }} />
              <span className="num" aria-label={t("guide_stale_episodes", { count: staleEpisodes })}>
                {staleEpisodes}
              </span>
            </span>
          )}
        </Segment>
        {guide && !yieldToPanel && (
          <>
            <Divider />
            <Segment segmentRef={nextRef} onClick={() => toggle("next")} expanded={open === "next"}>
              <span className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                {t("guide_next_label")}
              </span>
              <span className="font-medium" style={{ color: "var(--foreground)" }}>
                {guide.title}
              </span>
              <ChevronDown aria-hidden className="h-3 w-3" style={{ color: "var(--muted-foreground)" }} />
            </Segment>
          </>
        )}
        {allComplete && (
          <>
            <Divider />
            <span className="px-3 text-[11px]" style={{ color: "var(--good)" }}>
              {t("guide_all_complete")}
            </span>
          </>
        )}
        {yieldToPanel && (
          <>
            <Divider />
            <span className="px-3 text-[11px]" style={{ color: "var(--muted-foreground)" }}>
              {t("guide_next_in_panel")}
            </span>
          </>
        )}
      </div>
      <Popover
        open={open === "episodes"}
        onClose={() => setOpen(null)}
        anchorRef={episodesRef}
        align="center"
        width="w-[380px]"
      >
        <div className="p-2">
          <EpisodeList projectName={projectName} episodes={episodes} current={currentEpisode} onNavigate={navigate} />
        </div>
      </Popover>
      <Popover open={open === "next" && guide !== null} onClose={() => setOpen(null)} anchorRef={nextRef} align="center" width="w-[440px]">
        {guide && (
          <div className="p-3">
            <NextPanel guide={guide} projectName={projectName} onNavigate={navigate} />
          </div>
        )}
      </Popover>
    </div>
  );
}
