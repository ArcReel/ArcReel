import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";

import { API } from "@/api";
import { EPISODE_PLANNING_SLOTS, enqueueEpisodePlanning } from "@/actions/generation";
import { prefillAssistant } from "@/components/shared/DraftStatus";
import { OutputTruncationHint } from "@/components/shared/OutputTruncationHint";
import { GHOST_BTN_CLS } from "@/components/shared/darkroom-tokens";
import { StepActButton } from "@/components/workflow/StepActButton";
import type { StepAct } from "@/components/workflow/step-list";
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
  /** 本项目有分集规划在排队或执行。 */
  active: boolean;
}

/**
 * 「分集」视图右栏的 AI 规划分集：附加指令、「交给 Agent」与直接调用，进行中显示进度与停止，
 * 上一次规划失败时给出原因与出路，整本规划完后给出体量统计。
 */
export function EpisodePlanningPanel({ projectName, view, active }: Props) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [instruction, setInstruction] = useState(() => lastInstruction(projectName));
  const [submitting, setSubmitting] = useState(false);
  const [stopRequested, setStopRequested] = useState(false);
  const [finishingWindow, setFinishingWindow] = useState(false);
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

  // 停止的时机：执行中的那一窗在请求模型前才把下一窗排进队列，停止若落在这之前就取消不到它；
  // 停止请求未了结时，再看到排队的窗口就再停一次。
  useEffect(() => {
    if (active && stopRequested && queuedWindow) void API.stopEpisodePlanning(projectName).catch(() => undefined);
  }, [active, stopRequested, queuedWindow, projectName]);
  // 规划结束即了结停止请求，下一次规划从头开始
  if (!active && (stopRequested || finishingWindow)) {
    setStopRequested(false);
    setFinishingWindow(false);
  }

  const remaining = remainingUnits(view);
  const started = view.cut_units > 0;
  // 删除中间的切出集留下的空段不在接续规划的范围里，要用空段上的按钮单独规划
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
      <section
        aria-labelledby="episode-planning-title"
        className="space-y-2 rounded-md py-2 pl-3 pr-2"
        style={{ borderLeft: "3px solid var(--primary)", background: "color-mix(in oklab, var(--primary) 12%, transparent)" }}
      >
        <h3 id="episode-planning-title" className="flex items-center gap-1.5 text-[12.5px] font-medium text-foreground">
          <Loader2 aria-hidden className="h-3.5 w-3.5 text-primary motion-safe:animate-spin" />
          {t("dashboard:episode_planning_running", { percent })}
        </h3>
        <p className="m-0 text-[11.5px] leading-[1.6] text-muted-foreground">
          {finishingWindow ? t("dashboard:episode_planning_finishing") : t("dashboard:episode_planning_running_hint")}
        </p>
        {!stopRequested ? (
          <button type="button" className={GHOST_BTN_CLS} onClick={() => void stop()}>
            {t("dashboard:episode_planning_stop")}
          </button>
        ) : null}
      </section>
    );
  }

  if (remaining === 0 && started) {
    const stats = wholeSourceStats(view);
    return (
      <section aria-labelledby="episode-planning-title" className="space-y-2 border-t border-border pt-4">
        <h3 id="episode-planning-title" className="flex items-center gap-1.5 text-[12.5px] font-medium text-foreground">
          <CheckCircle2 aria-hidden className="h-3.5 w-3.5" style={{ color: "var(--good)" }} />
          {hasGaps ? t("dashboard:episode_planning_done_with_gaps") : t("dashboard:episode_planning_done")}
        </h3>
        {stats ? (
          <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11.5px]">
            <dt className="text-muted-foreground">{t("dashboard:episode_planning_stats_count")}</dt>
            <dd className="num m-0 text-subtle-foreground">{t("dashboard:episodes_view_episode_count", { count: stats.count })}</dd>
            <dt className="text-muted-foreground">{t("dashboard:episode_planning_stats_median")}</dt>
            <dd className="num m-0 text-subtle-foreground">
              {formatVolume(t, stats.medianUnits, view.unit)}
              {stats.medianSpokenSeconds !== null
                ? t("dashboard:episode_planning_stats_spoken", { spoken: formatSpoken(t, stats.medianSpokenSeconds) })
                : ""}
            </dd>
            {targetSeconds ? (
              <>
                <dt className="text-muted-foreground">{t("dashboard:episode_planning_stats_target")}</dt>
                <dd className="num m-0 text-subtle-foreground">{t("dashboard:episode_planning_stats_seconds", { count: targetSeconds })}</dd>
              </>
            ) : null}
          </dl>
        ) : null}
      </section>
    );
  }

  const agentAct: StepAct = {
    key: "agent",
    label: t("dashboard:guide_hand_to_agent"),
    kind: "agent",
    intent: { type: "agent", text: "" },
  };
  const aiAct: StepAct = {
    key: "ai",
    label: started ? t("dashboard:episode_planning_continue") : t("dashboard:episode_planning_start"),
    kind: "ai",
    intent: { type: "agent", text: "" },
  };
  const handToAgent = () =>
    prefillAssistant(
      withInstruction(t, started ? t("dashboard:guide_prefill_plan_continue") : t("dashboard:guide_prefill_plan"), instruction),
    );

  return (
    <section aria-labelledby="episode-planning-title" className="space-y-2 border-t border-border pt-4">
      <h3 id="episode-planning-title" className="text-[12.5px] font-medium text-foreground">
        {started ? t("dashboard:guide_plan_continue_title") : t("dashboard:guide_plan_title")}
      </h3>
      <p className="m-0 text-[11.5px] leading-[1.6] text-muted-foreground">
        {started
          ? t("dashboard:episode_planning_continue_detail", { volume: formatVolume(t, remaining, view.unit) })
          : t("dashboard:guide_plan_detail")}
      </p>
      {failure ? <FailureNote failure={failure} /> : null}
      <label className="block">
        <span className="mb-0.5 block text-[11px] text-muted-foreground">{t("dashboard:guide_instruction_label")}</span>
        <input
          value={instruction}
          onChange={(e) => updateInstruction(e.target.value)}
          placeholder={t("dashboard:guide_instruction_placeholder")}
          className="focus-ring w-full rounded-md px-2 py-1 text-[12px]"
          style={{
            background: "var(--muted)",
            border: "1px solid var(--border)",
            color: "var(--foreground)",
          }}
        />
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <StepActButton act={agentAct} onRun={handToAgent} />
        <StepActButton act={aiAct} onRun={() => void start()} busy={submitting} />
      </div>
    </section>
  );
}

function FailureNote({ failure }: { failure: PlanningFailure }) {
  const { t } = useTranslation("dashboard");
  return (
    <div role="alert" className="space-y-1.5 rounded-md p-2 text-[11.5px] leading-[1.6]" style={{ background: "color-mix(in oklab, var(--warn) 10%, transparent)" }}>
      <p className="m-0 flex gap-1.5 text-subtle-foreground">
        <AlertTriangle aria-hidden className="mt-[3px] h-3.5 w-3.5 shrink-0" style={{ color: "var(--warn)" }} />
        <span>{t("episode_planning_failed", { reason: failure.message })}</span>
      </p>
      {failure.truncated ? <OutputTruncationHint truncation={failure.truncated} /> : null}
    </div>
  );
}
