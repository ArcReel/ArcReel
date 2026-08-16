import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown } from "lucide-react";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import type { NarrationDelivery } from "@/types/workflow";
import { ProblemList } from "./ProblemList";
import { WorkflowStepRow } from "./WorkflowStepRow";
import { STEP_RAILS } from "./state-language";
import { blockerViews, problemViews } from "./problem-views";

/** TTS 没配好时后端给出的问题码；它挡住的只是 use_tts 这一条路径。 */
const TTS_NOT_CONFIGURED = "tts_not_configured";

interface Props {
  projectName: string;
  episode: number | null;
  /** 跳到画布上的该单元。 */
  onViewUnit?: (unitId: string) => void;
  /** 显式重生某一步的指定单元。 */
  onRegenerate?: (stepId: string, unitIds: string[]) => void;
}

/**
 * 项目工作流面板。
 *
 * 它只投影后端给出的权威计划：步骤、阻断、产物时效、任务与准入结论都照原样陈述，
 * 界面不自行推断下一步，也不把 stale、失败、缺失、受阻折成一个模糊的「有问题」。
 *
 * 默认收起成一行摘要。工作流状态是背景信息，不该长期占住创作区；需要判断「现在卡在哪」
 * 的时候展开，展开状态在会话内保留。
 */
export function WorkflowPanel({ projectName, episode, onViewUnit, onRegenerate }: Props) {
  const { t } = useTranslation("workflow");
  const panelId = useId();
  const alertId = useId();
  const [expanded, setExpanded] = useState(false);

  const plan = useWorkflowStore((s) => s.plan);
  const planKey = useWorkflowStore((s) => s.planKey);
  const loading = useWorkflowStore((s) => s.loading);
  const error = useWorkflowStore((s) => s.error);
  const narrationDelivery = useWorkflowStore((s) => s.narrationDelivery);
  const setNarrationDelivery = useWorkflowStore((s) => s.setNarrationDelivery);
  const confirmDurations = useWorkflowStore((s) => s.confirmDurations);
  const confirmedDurations = useWorkflowStore((s) => s.confirmedDurations);
  const refreshPlan = useWorkflowStore((s) => s.refreshPlan);
  const resetTarget = useWorkflowStore((s) => s.resetTarget);

  // 项目快照修订号与任务指纹是两条既有的变更信号：前者随 project.json / 剧本写入递增
  // （SSE 项目事件驱动），后者随任务轮询变化。计划同时依赖这两类事实，故两者都进依赖。
  const snapshotRevision = useProjectsStore((s) => s.projectSnapshotRevisions[projectName] ?? 0);
  const taskFingerprint = useTasksStore((s) =>
    s.tasks.map((task) => `${task.task_id}:${task.status}`).join("|"),
  );

  useEffect(() => {
    if (!projectName) return;
    void refreshPlan(projectName, episode);
  }, [
    projectName,
    episode,
    snapshotRevision,
    taskFingerprint,
    narrationDelivery,
    confirmedDurations,
    refreshPlan,
  ]);

  useEffect(() => () => resetTarget(), [resetTarget]);

  const currentKey = `${projectName}::${episode ?? ""}`;
  // 计划属于另一个目标时不拿它陈述当前目标——切集途中的旧事实比没有事实更糟。
  const shown = planKey === currentKey ? plan : null;

  const blockers = useMemo(
    () => (shown ? blockerViews(t, shown.blockers) : []),
    [shown, t],
  );
  const planProblems = useMemo(
    () => (shown ? problemViews(t, shown.problems, "plan") : []),
    [shown, t],
  );
  const ttsUnavailable = useMemo(() => {
    if (!shown) return null;
    const step = shown.steps.find((item) => item.id === "narration_delivery");
    const problem = step?.problems.find((item) => item.code === TTS_NOT_CONFIGURED);
    return problem ? (problemViews(t, [problem], "tts")[0] ?? null) : null;
  }, [shown, t]);

  const handleSelectDelivery = useCallback(
    (delivery: NarrationDelivery) => setNarrationDelivery(delivery),
    [setNarrationDelivery],
  );

  // 摘要行只复述后端给的下一步动作，不做任何本地推断。
  const headline = shown
    ? t("next_step", {
        step: t(`action_${shown.next_action.type}`, { defaultValue: t("action_unknown") }),
      })
    : loading
      ? t("plan_loading")
      : t("plan_unavailable");

  return (
    <section
      className="border-b px-4 py-2"
      style={{ borderColor: "var(--color-hairline)" }}
      data-testid="workflow-panel"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={panelId}
          onClick={() => setExpanded((value) => !value)}
          className="focus-ring flex items-center gap-1.5 rounded text-[12.5px] font-medium hover:opacity-80"
          style={{ color: "var(--color-text)" }}
        >
          <ChevronDown
            aria-hidden
            className="h-3.5 w-3.5 motion-safe:transition-transform"
            style={{ transform: expanded ? "rotate(0deg)" : "rotate(-90deg)" }}
          />
          {t("panel_title")}
        </button>
        <span className="min-w-0 flex-1 truncate text-[12px]" style={{ color: "var(--color-text-3)" }}>
          {headline}
        </span>
        {blockers.length > 0 && (
          <span
            className="rounded-full px-2 py-0.5 text-[11px]"
            style={{
              border: `1px solid ${STEP_RAILS.blocked.tone.ring}`,
              color: STEP_RAILS.blocked.tone.color,
            }}
          >
            {t("panel_blocker_count", { count: blockers.length })}
          </span>
        )}
      </div>

      {error && (
        <p className="mt-1 text-[11.5px]" role="status" style={{ color: "var(--color-text-3)" }}>
          {t("plan_refresh_failed")}
        </p>
      )}

      {expanded && (
        <div id={panelId} className="mt-2 space-y-3">
          {blockers.length > 0 && (
            <div
              role="alert"
              className="rounded-lg px-3 py-2"
              style={{
                background: STEP_RAILS.blocked.tone.soft,
                border: `1px solid ${STEP_RAILS.blocked.tone.ring}`,
              }}
            >
              <h3
                id={alertId}
                className="text-[12px] font-medium"
                style={{ color: STEP_RAILS.blocked.tone.color }}
              >
                {t("blockers_title", { count: blockers.length })}
              </h3>
              <ProblemList
                problems={blockers}
                labelledBy={alertId}
                className="mt-1 space-y-1.5 text-[12px]"
              />
            </div>
          )}

          {planProblems.length > 0 && (
            <ProblemList problems={planProblems} className="space-y-1.5 text-[12px]" />
          )}

          {shown ? (
            <ol className="m-0 list-none p-0">
              {shown.steps.map((step) => (
                <WorkflowStepRow
                  key={step.id}
                  step={step}
                  onViewUnit={onViewUnit}
                  onRegenerate={onRegenerate}
                  onConfirmDurations={confirmDurations}
                  busy={loading}
                  narration={
                    step.id === "narration_delivery"
                      ? {
                          choice: shown.narration_delivery,
                          ttsUnavailable,
                          onSelect: handleSelectDelivery,
                        }
                      : undefined
                  }
                />
              ))}
            </ol>
          ) : (
            <p className="text-[12px]" style={{ color: "var(--color-text-3)" }}>
              {loading ? t("plan_loading") : t("plan_unavailable")}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
