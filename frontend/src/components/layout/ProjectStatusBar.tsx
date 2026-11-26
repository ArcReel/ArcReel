import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { episodeScriptRevisionKey, useProjectsStore } from "@/stores/projects-store";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import {
  isResourceBusy,
  selectStatusBarTaskSignal,
  useActiveResourceIds,
  useTasksStore,
} from "@/stores/tasks-store";
import type { EpisodeMeta } from "@/types";
import type { EpisodeNextStep, WorkflowNextAction, WorkflowStatus } from "@/types/workflow";
import { errMsg } from "@/utils/async";
import { cn } from "cn";
import { episodeDisplayName } from "@/utils/episode-display";
import { lastInstruction, rememberInstruction } from "@/utils/last-instruction";
import {
  actionPhrase,
  episodeNeedsReplan,
  projectNextGuide,
  withInstruction,
  type GuideButton,
  type ProjectNextGuide,
} from "./project-guide";
import { MigrationReportNotice } from "./MigrationReportNotice";

const STATUS_REFRESH_DEBOUNCE_MS = 250;
const EPISODE_LIST_WINDOW_SIZE = 32;
const EPISODE_LIST_ESTIMATED_ROW_HEIGHT = 28;
const EPISODE_LIST_OVERSCAN = 8;

const SHELL = "inline-flex h-7 items-center overflow-hidden rounded-full border";
const SHELL_FLAT = cn(SHELL, "border-border bg-card");
const SHELL_WARM = cn(SHELL, "border-warn/30 bg-warn/10");
const SEGMENT =
  "focus-ring inline-flex h-full items-center gap-1.5 whitespace-nowrap px-3 text-xs transition-colors duration-fast enabled:hover:bg-foreground/5 disabled:cursor-default";

type Open = "episodes" | "next" | null;

function isDocumentHidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

function useCurrentEpisodeId(): number | null {
  const [location] = useLocation();
  const match = /^\/episodes\/(\d+)/.exec(location);
  return match ? Number(match[1]) : null;
}

function episodeIdFromAction(action: WorkflowNextAction | undefined): number | null {
  const value = action?.args?.episode_id;
  return typeof value === "number" ? value : null;
}

function useProjectWorkflowStatus(
  projectName: string,
  enabled: boolean,
  currentEpisode: number | null,
  episodes: readonly EpisodeMeta[],
): WorkflowStatus | null {
  const [loaded, setLoaded] = useState<{ projectName: string; status: WorkflowStatus | null }>({
    projectName,
    status: null,
  });
  const status = loaded.projectName === projectName ? loaded.status : null;
  const targetEpisode = status?.target?.episode ?? episodeIdFromAction(status?.next_action);
  const yieldToPanel =
    targetEpisode !== null && currentEpisode !== null && targetEpisode === currentEpisode;
  const currentEpisodeCompleted =
    currentEpisode !== null &&
    episodes.some((ep) => ep.episode === currentEpisode && ep.status === "completed");

  const projectWideRevision = useProjectsStore((s) => s.projectWideRevisions[projectName] ?? 0);
  const targetEpisodeRevision = useProjectsStore((s) =>
    targetEpisode !== null
      ? (s.episodeRevisions[episodeScriptRevisionKey(projectName, targetEpisode)] ?? 0)
      : (s.projectSnapshotRevisions[projectName] ?? 0),
  );
  const taskSignal = useTasksStore((s) =>
    selectStatusBarTaskSignal(s, projectName, targetEpisode, yieldToPanel),
  );

  const liveSignal = yieldToPanel
    ? `yielded::${projectWideRevision}::${currentEpisodeCompleted ? "done" : "open"}`
    : `active::${projectWideRevision}::${targetEpisodeRevision}::${episodes.find((ep) => ep.status !== "completed")?.episode ?? ""}::${taskSignal}`;
  const settledLiveSignal = useDebouncedValue(liveSignal, STATUS_REFRESH_DEBOUNCE_MS);
  const liveSignalRef = useRef(liveSignal);
  useEffect(() => {
    liveSignalRef.current = liveSignal;
  }, [liveSignal]);

  const controllerRef = useRef<AbortController | null>(null);
  const lastFetchedRef = useRef<{ projectName: string; signal: string } | null>(null);
  const prevYieldedRef = useRef(yieldToPanel);
  const pendingVisibleRefreshRef = useRef(false);

  const fetchStatus = useCallback(
    (signalSnapshot: string) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      lastFetchedRef.current = { projectName, signal: signalSnapshot };
      API.getWorkflowStatus(projectName, { signal: controller.signal })
        .then((next) => {
          if (!controller.signal.aborted) setLoaded({ projectName, status: next });
        })
        .catch(() => {
          if (!controller.signal.aborted) setLoaded({ projectName, status: null });
        });
    },
    [projectName],
  );

  useEffect(() => {
    if (!enabled) {
      controllerRef.current?.abort();
      controllerRef.current = null;
      lastFetchedRef.current = null;
      pendingVisibleRefreshRef.current = false;
      return;
    }
    if (isDocumentHidden()) {
      pendingVisibleRefreshRef.current = true;
      return () => {
        controllerRef.current?.abort();
      };
    }
    pendingVisibleRefreshRef.current = false;
    fetchStatus(liveSignalRef.current);
    return () => {
      controllerRef.current?.abort();
    };
  }, [enabled, projectName, fetchStatus]);

  useEffect(() => {
    const wasYielded = prevYieldedRef.current;
    prevYieldedRef.current = yieldToPanel;
    if (!enabled) return;
    if (!wasYielded && yieldToPanel) {
      if (lastFetchedRef.current?.projectName === projectName) {
        lastFetchedRef.current = { projectName, signal: liveSignalRef.current };
      }
      return;
    }
    const prev = lastFetchedRef.current;
    if (settledLiveSignal !== liveSignalRef.current || (prev?.projectName === projectName && prev.signal === settledLiveSignal)) return;
    if (isDocumentHidden()) {
      pendingVisibleRefreshRef.current = true;
      return;
    }
    pendingVisibleRefreshRef.current = false;
    fetchStatus(settledLiveSignal);
  }, [enabled, projectName, settledLiveSignal, yieldToPanel, fetchStatus]);

  useEffect(() => {
    if (!enabled || typeof document === "undefined") return;
    const onVisibilityChange = () => {
      if (document.visibilityState !== "visible" || !pendingVisibleRefreshRef.current) return;
      pendingVisibleRefreshRef.current = false;
      fetchStatus(liveSignalRef.current);
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [enabled, fetchStatus]);

  return enabled ? status : null;
}

function Divider({ warm }: { warm?: boolean }) {
  return <span aria-hidden className={cn("h-3.5 w-px", warm ? "bg-warn/30" : "bg-border")} />;
}

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
  if (episodeNeedsReplan(episode)) return "bg-warn";
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
  const listRef = useRef<HTMLOListElement | null>(null);
  const snapshotRevision = useProjectsStore((s) => s.projectSnapshotRevisions[projectName] ?? 0);
  const scopeKey = `${projectName}::${snapshotRevision}`;

  const [windowRange, setWindowRange] = useState<{ start: number; end: number }>({
    start: 0,
    end: EPISODE_LIST_WINDOW_SIZE,
  });
  const [cache, setCache] = useState<{
    scopeKey: string;
    steps: Map<number, EpisodeNextStep>;
    failed: Set<number>;
  }>({
    scopeKey,
    steps: new Map(),
    failed: new Set(),
  });

  const controllerRef = useRef<AbortController | null>(null);
  const requestedRef = useRef<{ scopeKey: string; ids: Set<number> }>({ scopeKey, ids: new Set() });

  useEffect(() => {
    const controller = new AbortController();
    controllerRef.current = controller;
    requestedRef.current = { scopeKey, ids: new Set() };
    return () => {
      controller.abort();
      if (controllerRef.current === controller) controllerRef.current = null;
    };
  }, [scopeKey]);

  const activeSteps = cache.scopeKey === scopeKey ? cache.steps : null;

  const missingInWindow = useMemo(() => {
    const start = Math.max(0, Math.min(windowRange.start, episodes.length));
    const end = Math.max(start, Math.min(windowRange.end, episodes.length));
    const ids: number[] = [];
    for (let index = start; index < end && ids.length < EPISODE_LIST_WINDOW_SIZE; index += 1) {
      const ep = episodes[index];
      if (!ep || ep.status === "completed") continue;
      if (activeSteps?.has(ep.episode)) continue;
      if (cache.scopeKey === scopeKey && cache.failed.has(ep.episode)) continue;
      ids.push(ep.episode);
    }
    return ids;
  }, [episodes, windowRange, activeSteps, cache.scopeKey, cache.failed, scopeKey]);

  const missingInWindowSet = useMemo(() => new Set(missingInWindow), [missingInWindow]);

  useEffect(() => {
    const controller = controllerRef.current;
    if (!controller || controller.signal.aborted || missingInWindow.length === 0) return;
    if (requestedRef.current.scopeKey !== scopeKey) {
      requestedRef.current = { scopeKey, ids: new Set() };
    }
    const batch = missingInWindow.filter((id) => !requestedRef.current.ids.has(id));
    if (batch.length === 0) return;
    for (const id of batch) requestedRef.current.ids.add(id);

    API.getEpisodeNextSteps(projectName, {
      signal: controller.signal,
      episodes: batch,
    })
      .then((page) => {
        if (controller.signal.aborted) return;
        setCache((prev) => {
          const base =
            prev.scopeKey === scopeKey
              ? prev
              : { scopeKey, steps: new Map<number, EpisodeNextStep>(), failed: new Set<number>() };
          const steps = new Map(base.steps);
          const failed = new Set(base.failed);
          const returned = new Set<number>();
          for (const step of page.episodes) {
            steps.set(step.episode, step);
            returned.add(step.episode);
          }
          for (const id of batch) {
            if (!returned.has(id)) failed.add(id);
          }
          return { scopeKey, steps, failed };
        });
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setCache((prev) => {
          const base =
            prev.scopeKey === scopeKey
              ? prev
              : { scopeKey, steps: new Map<number, EpisodeNextStep>(), failed: new Set<number>() };
          const failed = new Set(base.failed);
          for (const id of batch) failed.add(id);
          return { ...base, failed };
        });
      });
  }, [projectName, scopeKey, missingInWindow]);

  const handleScroll = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    const firstRow = el.firstElementChild as HTMLElement | null;
    const gap = parseFloat(getComputedStyle(el).rowGap) || 0;
    const rowHeight = Math.max(firstRow?.offsetHeight || EPISODE_LIST_ESTIMATED_ROW_HEIGHT, 16) + gap;
    const visibleCount = Math.max(
      Math.ceil((el.clientHeight || rowHeight * 12) / rowHeight),
      EPISODE_LIST_WINDOW_SIZE - EPISODE_LIST_OVERSCAN * 2,
    );
    const firstVisible = Math.floor(el.scrollTop / rowHeight);
    const start = Math.max(0, firstVisible - EPISODE_LIST_OVERSCAN);
    const end = Math.min(episodes.length, Math.max(start + EPISODE_LIST_WINDOW_SIZE, firstVisible + visibleCount + EPISODE_LIST_OVERSCAN));
    setWindowRange((prev) => (prev.start === start && prev.end === end ? prev : { start, end }));
  }, [episodes.length]);

  const rowNote = (episode: EpisodeMeta): string => {
    if (episode.status === "completed") return t("dashboard:guide_episode_completed");
    const step = activeSteps?.get(episode.episode);
    if (step) {
      if (step.plan_stale) return t("dashboard:guide_episode_plan_stale");
      if (step.episode_complete) return t("dashboard:guide_episode_completed");
      if (!step.next_action || step.next_action.type === "none") return t("workflow:action_none");
      return t("dashboard:guide_episode_next", { step: actionPhrase(t, step.next_action.type) });
    }
    if (missingInWindowSet.has(episode.episode)) return "…";
    return "";
  };

  return (
    <ol
      ref={listRef}
      onScroll={handleScroll}
      className="relative flex max-h-90 flex-col gap-0.5 overflow-y-auto"
    >
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

export function ProjectStatusBar({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const [, setLocation] = useLocation();
  const demoMode = useDemoWorkbench();
  const project = useProjectsStore((s) => s.currentProjectData);
  const currentEpisode = useCurrentEpisodeId();
  const [open, setOpen] = useState<Open>(null);

  const summary = project?.status;
  const needsRepair = summary?.needs_repair === true;
  const episodes = useMemo(() => project?.episodes ?? [], [project?.episodes]);
  const workflowStatus = useProjectWorkflowStatus(
    projectName,
    !demoMode && !needsRepair && Boolean(summary),
    currentEpisode,
    episodes,
  );
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
  const replanEpisodes = episodes.filter(episodeNeedsReplan).length;
  const isAd = project.content_mode === "ad";
  const progressText =
    total === 0
      ? t("guide_no_episodes")
      : isAd
        ? done >= total
          ? t("guide_ad_completed")
          : t("guide_ad_incomplete")
        : t("guide_progress", { done, total });
  const yieldToPanel = guide !== null && currentEpisode !== null && guide.episodeId === currentEpisode;
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
          {replanEpisodes > 0 && (
            <span
              className="inline-flex items-center gap-1 text-warn"
              title={t("guide_needs_replan_episodes", { count: replanEpisodes })}
            >
              <span aria-hidden className="size-1.5 rounded-full bg-warn" />
              <span className="num" aria-label={t("guide_needs_replan_episodes", { count: replanEpisodes })}>
                {replanEpisodes}
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
