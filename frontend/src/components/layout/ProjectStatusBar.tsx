import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";
import { AlertTriangle, Bot, ChevronDown, Loader2 } from "lucide-react";

import { API } from "@/api";
import { EPISODE_PLANNING_SLOTS, enqueueEpisodePlanning } from "@/actions/generation";
import { ApiRequestError } from "@/api/errors";
import { episodesViewPath } from "@/components/canvas/episodes/episodes-view-model";
import { prefillAssistant } from "@/components/shared/prefill-assistant";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { StepActButton } from "@/components/workflow/StepActButton";
import type { StepAct } from "@/components/workflow/step-list";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { isResourceBusy, useActiveResourceIds, useTasksStore } from "@/stores/tasks-store";
import type { EpisodeMeta } from "@/types";
import type { EpisodeNextStep, WorkflowStatus } from "@/types/workflow";
import { errMsg } from "@/utils/async";
import { cn } from "cn";
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
import { MigrationReportNotice } from "./MigrationReportNotice";

/** Millisecond task-change merge windows, matching episode panels. */
const STATUS_REFRESH_DEBOUNCE_MS = 250;

/** Flat states without inset/raised highlights, avoiding segmented-toggle appearance. */
const SHELL = "inline-flex h-7 items-center overflow-hidden rounded-full border";
const SHELL_FLAT = cn(SHELL, "border-border bg-card");
const SHELL_WARM = cn(SHELL, "border-warn/30 bg-warn/10");
/** Native status buttons rendered through PopoverTrigger render. */
const SEGMENT =
  "focus-ring inline-flex h-full items-center gap-1.5 whitespace-nowrap px-3 text-xs transition-colors duration-fast enabled:hover:bg-foreground/5 disabled:cursor-default";

type Open = "episodes" | "next" | null;

/** Current episode IDs, null outside episodes. */
function useCurrentEpisodeId(): number | null {
  const [location] = useLocation();
  const match = /^\/episodes\/(\d+)/.exec(location);
  return match ? Number(match[1]) : null;
}

/** Project production state resolves on snapshots/current-project task changes. */
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
        // Failed solves temporarily hide right segments only, preserving workbenches and retrying next snapshots.
        if (!controller.signal.aborted) setStatus(null);
      });
    return () => controller.abort();
  }, [projectName, enabled, snapshotRevision, settledTaskFingerprint]);

  return enabled ? status : null;
}

function Divider({ warm }: { warm?: boolean }) {
  return <span aria-hidden className={cn("h-3.5 w-px", warm ? "bg-warn/30" : "bg-border")} />;
}

/** 15px progress rings, matching numeric badges. */
function Ring({ done, total }: { done: number; total: number }) {
  const r = 6;
  const c = 2 * Math.PI * r;
  const f = total ? Math.min(done / total, 1) : 0;
  return (
    <svg width="15" height="15" viewBox="0 0 15 15" aria-hidden>
      <circle cx="7.5" cy="7.5" r={r} fill="none" className="stroke-border" strokeWidth="2.2" />
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

function episodeDotClass(episode: EpisodeMeta): string {
  if (episode.status === "completed") return "bg-good";
  if (episodeNeedsUpdate(episode)) return "bg-warn";
  if (episode.status === "in_production") return "bg-primary";
  return "bg-muted-foreground";
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
  return <StepActButton act={act} onRun={onRun} asLink={!primary} busy={submitting} />;
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
    <div className="flex flex-col gap-2">
      <p className="text-xs text-subtle-foreground">{guide.detail}</p>
      {guide.instruction && (
        <label className="flex flex-col gap-1">
          <span className="text-xs text-subtle-foreground">{t("guide_instruction_label")}</span>
          <Input
            value={instruction}
            onChange={(e) => updateInstruction(e.target.value)}
            placeholder={t("guide_instruction_placeholder")}
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
          <span className="inline-flex flex-wrap items-center gap-1.5 text-xs text-subtle-foreground">
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
    <ol className="relative flex max-h-90 flex-col gap-0.5 overflow-y-auto">
      {episodes.map((episode, index) => (
        <li key={episode.episode}>
          <button
            type="button"
            onClick={() => onNavigate(`/episodes/${episode.episode}`)}
            aria-current={current === episode.episode ? "page" : undefined}
            className={cn(
              "focus-ring flex w-full items-center gap-2 rounded-sm px-1.5 py-1 text-left text-xs transition-colors duration-fast",
              current === episode.episode ? "bg-primary/10" : "hover:bg-foreground/5",
            )}
          >
            <span aria-hidden className={cn("size-2 shrink-0 rounded-full", episodeDotClass(episode))} />
            <span className="w-12 shrink-0 text-subtle-foreground tabular-nums">
              {t("common:episode_position_name", { position: index + 1 })}
            </span>
            <span className="min-w-0 flex-1 truncate text-foreground">{episodeDisplayName(episodes, episode.episode, t)}</span>
            <span className="shrink-0 text-subtle-foreground">{rowNote(episode)}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

/** Migration failures warm entire bars; Retry reruns upgrades, counts appear in titles and causes expand. */
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
      // Upgrades completed; failed refreshes notify failure only, without simultaneous success.
      if ((await refreshAfterWrite(projectName, t)) === "success") {
        useAppStore.getState().pushToast(t("migration_retry_succeeded"), "success");
      }
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
    <div ref={anchorRef} className={SHELL_WARM}>
      <Popover open={open} onOpenChange={setOpen}>
        {/* Narrow headers retain icons/Retry, moving titles into accessible names/overlays. */}
        <PopoverTrigger render={<button type="button" className={SEGMENT} aria-label={title} />}>
          <AlertTriangle aria-hidden className="size-3.5 text-warn" />
          <span aria-hidden className="hidden text-foreground @3xl/header:inline">
            {title}
          </span>
          <ChevronDown aria-hidden className="size-3 text-muted-foreground" />
        </PopoverTrigger>
        <PopoverContent anchor={anchorRef} className="w-110">
          <div className="flex flex-col gap-2" role="alert">
            <p className="text-xs font-semibold text-foreground">
              {failures > 0 ? t("migration_retry_failed_heading") : t("migration_repair_title")}
            </p>
            <p className="text-xs text-subtle-foreground">
              {failures > 0 ? t("migration_retry_failed_body") : t("migration_repair_body")}
            </p>
            {shownReason ? <p className="font-mono text-xs break-words text-subtle-foreground">{shownReason}</p> : null}
            {failures > 0 && (
              <div className="flex">
                <Button variant="outline" size="sm" onClick={() => prefillAssistant(t("migration_repair_prefill"))}>
                  <Bot aria-hidden data-icon="inline-start" />
                  {t("migration_hand_to_agent")}
                </Button>
              </div>
            )}
          </div>
        </PopoverContent>
      </Popover>
      <Divider warm />
      <div className="px-1.5">
        <Button size="xs" onClick={() => void retry()} disabled={running}>
          {running && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
          {running ? t("migration_retry_running") : t("migration_retry")}
        </Button>
      </div>
    </div>
  );
}

/**
 * Center header bars: left episode progress/per-episode overlays, right project next steps/explanations/actions.
 * Upgrade failures replace bars with migration states; unconfirmed reports add dismissible upgrade notices.
 * Project backend summaries/states only, never infer next steps locally.
 */
export function ProjectStatusBar({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const [, setLocation] = useLocation();
  const demoMode = useDemoWorkbench();
  const project = useProjectsStore((s) => s.currentProjectData);
  const currentEpisode = useCurrentEpisodeId();
  const [open, setOpen] = useState<Open>(null);

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

  const openChange = (key: Exclude<Open, null>) => (next: boolean) =>
    setOpen((value) => (next ? key : value === key ? null : value));
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
  // When project next steps target current episodes, yield to episode panels rather than duplicate actions.
  const yieldToPanel = guide !== null && currentEpisode !== null && guide.episodeId === currentEpisode;
  // All episodes complete without remaining sources: right states All completed without actions; ad left already says Short film completed.
  const allComplete = guide === null && !isAd && workflowStatus?.content?.project_complete === true;

  const migrationReport = workflowStatus?.migration_report ?? null;
  const bar = (
    <div className={SHELL_FLAT}>
      <Popover open={open === "episodes"} onOpenChange={openChange("episodes")}>
        <PopoverTrigger
          render={<button type="button" className={SEGMENT} />}
          disabled={total === 0 || isAd}
        >
          <Ring done={done} total={total} />
          <span className="num text-subtle-foreground">{progressText}</span>
          {staleEpisodes > 0 && (
            <span className="inline-flex items-center gap-1 text-warn" title={t("guide_stale_episodes", { count: staleEpisodes })}>
              <span aria-hidden className="size-1.5 rounded-full bg-warn" />
              <span className="num" aria-label={t("guide_stale_episodes", { count: staleEpisodes })}>
                {staleEpisodes}
              </span>
            </span>
          )}
        </PopoverTrigger>
        <PopoverContent className="w-95">
          <EpisodeList projectName={projectName} episodes={episodes} current={currentEpisode} onNavigate={navigate} />
        </PopoverContent>
      </Popover>
      {guide && !yieldToPanel && (
        <>
          <Divider />
          <Popover open={open === "next"} onOpenChange={openChange("next")}>
            <PopoverTrigger render={<button type="button" className={SEGMENT} />}>
              <span className="text-muted-foreground">{t("guide_next_label")}</span>
              <span className="font-medium text-foreground">{guide.title}</span>
              <ChevronDown aria-hidden className="size-3 text-muted-foreground" />
            </PopoverTrigger>
            <PopoverContent className="w-110">
              <NextPanel guide={guide} projectName={projectName} onNavigate={navigate} />
            </PopoverContent>
          </Popover>
        </>
      )}
      {allComplete && (
        <>
          <Divider />
          <span className="px-3 text-xs text-good">{t("guide_all_complete")}</span>
        </>
      )}
      {yieldToPanel && (
        <>
          <Divider />
          <span className="px-3 text-xs text-muted-foreground">{t("guide_next_in_panel")}</span>
        </>
      )}
    </div>
  );
  if (!migrationReport) return bar;
  return (
    <div className="flex min-w-0 items-center gap-2">
      {bar}
      <MigrationReportNotice projectName={projectName} report={migrationReport} episodes={episodes} />
    </div>
  );
}
