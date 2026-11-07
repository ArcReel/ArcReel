import { useEffect, useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Bot, CheckCircle2, Loader2, Sparkles } from "lucide-react";

import { API } from "@/api";
import { EPISODE_PLANNING_SLOTS, enqueueEpisodePlanning } from "@/actions/generation";
import { prefillAssistant } from "@/components/shared/prefill-assistant";
import { OutputTruncationHint } from "@/components/shared/OutputTruncationHint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PopoverDescription, PopoverTitle } from "@/components/ui/popover";
import { withInstruction } from "@/components/layout/project-guide";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { isResourceBusy, useTasksStore } from "@/stores/tasks-store";
import type { EpisodesView } from "@/types";
import { errMsg } from "@/utils/async";
import { lastInstruction, rememberInstruction } from "@/utils/last-instruction";

import {
  lastPlanningFailure,
  remainingUnits,
  wholeSourceStats,
  type PlanningFailure,
} from "./episode-planning-model";
import { formatSpoken, formatVolume } from "./episodes-view-model";

interface Props {
  projectName: string;
  view: EpisodesView;
  /** Episode planning is queued or running for this project. */
  active: boolean;
  /** Planning submitted or handed to Agent; callers close the popover accordingly. */
  onStarted?: () => void;
}

/**
 * AI episode-planning header popover in Episodes view: extra instructions, Agent handoff/direct invocation, running progress/stop,
 * last-failure reasons/recovery paths, and volume statistics after whole-source completion.
 */
export function EpisodePlanningPanel({ projectName, view, active, onStarted }: Props) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [instruction, setInstruction] = useState(() => lastInstruction(projectName));
  const [submitting, setSubmitting] = useState(false);
  const [stopRequested, setStopRequested] = useState(false);
  const [finishingWindow, setFinishingWindow] = useState(false);
  const instructionId = useId();
  const tasks = useTasksStore((s) => s.tasks);
  const targetSeconds = useProjectsStore((s) => s.currentProjectData?.episode_target_duration ?? null);
  const failure = useMemo(() => (active ? null : lastPlanningFailure(tasks, projectName)), [active, tasks, projectName]);
  const queuedWindow = tasks.some(
    (task) =>
      task.project_name === projectName &&
      task.task_type === "text_episode_plan" &&
      task.status === "queued" &&
      (EPISODE_PLANNING_SLOTS as readonly string[]).includes(task.resource_id),
  );

  // Stop timing: running windows queue the next only before model requests; earlier stops cannot cancel it;
  // while a stop request remains unresolved, stop again if another queued window appears.
  useEffect(() => {
    if (active && stopRequested && queuedWindow) void API.stopEpisodePlanning(projectName).catch(() => undefined);
  }, [active, stopRequested, queuedWindow, projectName]);
  // Planning completion resolves the stop request; the next planning run starts fresh.
  if (!active && (stopRequested || finishingWindow)) {
    setStopRequested(false);
    setFinishingWindow(false);
  }

  const remaining = remainingUnits(view);
  const started = view.cut_units > 0;
  // Gaps from deleting middle cut episodes are outside continuation scope; plan them individually through gap buttons.
  const hasGaps = view.files.some((file) => file.segments.some((segment) => segment.gap && segment.units > 0));
  const percent = view.units === 0 ? 0 : Math.round((view.cut_units / view.units) * 100);

  const updateInstruction = (value: string) => {
    setInstruction(value);
    rememberInstruction(projectName, value);
  };

  const start = async () => {
    if (submitting) return;
    if (EPISODE_PLANNING_SLOTS.some((slot) => isResourceBusy("text_episode_plan", projectName, slot))) {
      useAppStore.getState().pushToast(t("dashboard:episode_planning_busy"), "error");
      return;
    }
    setSubmitting(true);
    try {
      await enqueueEpisodePlanning(projectName, instruction.trim() || null);
      onStarted?.();
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setSubmitting(false);
    }
  };

  const stop = async () => {
    setStopRequested(true);
    try {
      const result = await API.stopEpisodePlanning(projectName);
      setFinishingWindow(result.running.length > 0);
    } catch (err) {
      setStopRequested(false);
      useAppStore.getState().pushToast(errMsg(err), "error");
    }
  };

  if (view.files.length === 0) return null;

  if (active) {
    return (
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-1.5">
          <Loader2 aria-hidden className="size-3.5 shrink-0 animate-spin text-primary" />
          <PopoverTitle>{t("dashboard:episode_planning_running", { percent })}</PopoverTitle>
        </div>
        <PopoverDescription>
          {finishingWindow ? t("dashboard:episode_planning_finishing") : t("dashboard:episode_planning_running_hint")}
        </PopoverDescription>
        {!stopRequested ? (
          <Button variant="outline" size="sm" className="self-start" onClick={() => void stop()}>
            {t("dashboard:episode_planning_stop")}
          </Button>
        ) : null}
      </div>
    );
  }

  if (remaining === 0 && started) {
    const stats = wholeSourceStats(view);
    return (
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-1.5">
          <CheckCircle2 aria-hidden className="size-3.5 shrink-0 text-good" />
          <PopoverTitle>
            {hasGaps ? t("dashboard:episode_planning_done_with_gaps") : t("dashboard:episode_planning_done")}
          </PopoverTitle>
        </div>
        {stats ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
            <dt className="text-muted-foreground">{t("dashboard:episode_planning_stats_count")}</dt>
            <dd className="num text-subtle-foreground">
              {t("dashboard:episodes_view_episode_count", { count: stats.count })}
            </dd>
            <dt className="text-muted-foreground">{t("dashboard:episode_planning_stats_median")}</dt>
            <dd className="num text-subtle-foreground">
              {formatVolume(t, stats.medianUnits, view.unit)}
              {stats.medianSpokenSeconds !== null
                ? t("dashboard:episode_planning_stats_spoken", { spoken: formatSpoken(t, stats.medianSpokenSeconds) })
                : ""}
            </dd>
            {targetSeconds ? (
              <>
                <dt className="text-muted-foreground">{t("dashboard:episode_planning_stats_target")}</dt>
                <dd className="num text-subtle-foreground">
                  {t("dashboard:episode_planning_stats_seconds", { count: targetSeconds })}
                </dd>
              </>
            ) : null}
          </dl>
        ) : null}
      </div>
    );
  }

  const handToAgent = () => {
    prefillAssistant(
      withInstruction(t, started ? t("dashboard:guide_prefill_plan_continue") : t("dashboard:guide_prefill_plan"), instruction),
    );
    onStarted?.();
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <PopoverTitle>{started ? t("dashboard:guide_plan_continue_title") : t("dashboard:guide_plan_title")}</PopoverTitle>
        <PopoverDescription>
          {started
            ? t("dashboard:episode_planning_continue_detail", { volume: formatVolume(t, remaining, view.unit) })
            : t("dashboard:guide_plan_detail")}
        </PopoverDescription>
      </div>
      {failure ? <FailureNote failure={failure} /> : null}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={instructionId}>{t("dashboard:guide_instruction_label")}</Label>
        <Input
          id={instructionId}
          value={instruction}
          onChange={(e) => updateInstruction(e.target.value)}
          placeholder={t("dashboard:guide_instruction_placeholder")}
        />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={handToAgent}>
          <Bot aria-hidden data-icon="inline-start" />
          {t("dashboard:guide_hand_to_agent")}
        </Button>
        <Button variant="outline" size="sm" disabled={submitting} onClick={() => void start()}>
          {submitting ? (
            <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
          ) : (
            <Sparkles aria-hidden data-icon="inline-start" />
          )}
          {started ? t("dashboard:episode_planning_continue") : t("dashboard:episode_planning_start")}
        </Button>
      </div>
    </div>
  );
}

function FailureNote({ failure }: { failure: PlanningFailure }) {
  const { t } = useTranslation("dashboard");
  return (
    <div role="alert" className="flex flex-col gap-1.5 rounded-md bg-warn/10 p-2 text-xs">
      <p className="flex gap-1.5 text-subtle-foreground">
        <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0 text-warn" />
        <span>{t("episode_planning_failed", { reason: failure.message })}</span>
      </p>
      {failure.truncated ? <OutputTruncationHint truncation={failure.truncated} /> : null}
    </div>
  );
}
