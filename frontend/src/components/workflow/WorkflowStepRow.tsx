import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { NarrationDelivery, WorkflowPlanStep } from "@/types/workflow";
import { ArtifactMeter, artifactCounts } from "./ArtifactMeter";
import { BatchAdmissionSummary } from "./BatchAdmissionSummary";
import { NarrationDeliveryChoice } from "./NarrationDeliveryChoice";
import { ProblemList } from "./ProblemList";
import { StaleArtifacts } from "./StaleArtifacts";
import { TaskChips } from "./TaskChips";
import { INLINE_ACTION_CLS, STEP_RAILS } from "./state-language";
import { nextStepForAction, problemViews, type ProblemView } from "./problem-views";

/** 轨道刻度：步骤进度这一轴的唯一图形。产物用填充块、任务用描边胶囊，三者不共用形状。 */
function RailMark({ step }: { step: WorkflowPlanStep }) {
  const rail = STEP_RAILS[step.state];
  const base = "mt-1 h-2.5 w-2.5 shrink-0 rounded-full";
  if (rail.mark === "gap") {
    return (
      <span aria-hidden className={`${base} rounded-none`} style={{ background: rail.tone.color, height: 2, marginTop: 9 }} />
    );
  }
  if (rail.mark === "solid") {
    return <span aria-hidden className={base} style={{ background: rail.tone.color }} />;
  }
  if (rail.mark === "pulse") {
    return (
      <span
        aria-hidden
        className={`${base} motion-safe:animate-pulse`}
        style={{ background: rail.tone.color, boxShadow: `0 0 0 3px ${rail.tone.glow}` }}
      />
    );
  }
  return (
    <span
      aria-hidden
      className={base}
      style={{ border: `1px ${rail.mark === "dashed" ? "dashed" : "solid"} ${rail.tone.ring}` }}
    />
  );
}

export interface NarrationChoiceBinding {
  choice: import("@/types/workflow").WorkflowNarrationDeliveryChoice;
  ttsUnavailable?: ProblemView | null;
  onSelect: (delivery: NarrationDelivery) => void;
}

interface Props {
  step: WorkflowPlanStep;
  /** 跳到画布上的该单元；预览与版本历史都在那里，面板不另起一套播放器。 */
  onViewUnit?: (unitId: string) => void;
  /** 显式重生：这一步的指定单元。会花钱，必须由用户按下。 */
  onRegenerate?: (stepId: string, unitIds: string[]) => void;
  /** 按当前档位确认整批并重新求解。 */
  onConfirmDurations?: (durations: Record<string, number>) => void;
  /** 仅 `narration_delivery` 步骤绑定。 */
  narration?: NarrationChoiceBinding;
  busy?: boolean;
}

/**
 * 一个编排步骤。
 *
 * 步骤本身只报进度，产物、任务、准入与执行结果各自成段——它们回答的是不同问题，
 * 混成一个状态词就等于替用户下结论。跳过的步骤保留在列表里并说明「本模式不涉及」，
 * 因为「不需要做」和「还没做」在用户那里是两件事。
 */
export function WorkflowStepRow({
  step,
  onViewUnit,
  onRegenerate,
  onConfirmDurations,
  narration,
  busy,
}: Props) {
  const { t } = useTranslation("workflow");
  const headingId = useId();
  const skipped = step.state === "skipped";
  const counts = artifactCounts(step.artifacts);
  const staleIds = counts?.stale ?? [];
  const admission = step.admission;
  const confirmationTiers = admission?.confirmation?.tiers ?? [];

  const confirmDurations = () => {
    const durations: Record<string, number> = {};
    for (const tier of confirmationTiers) {
      if (tier.request_duration_seconds == null) continue;
      for (const unitId of tier.unit_ids) durations[unitId] = tier.request_duration_seconds;
    }
    onConfirmDurations?.(durations);
  };

  return (
    <li className="flex gap-2.5" data-testid={`workflow-step-${step.id}`}>
      <RailMark step={step} />
      <div className="min-w-0 flex-1 space-y-1.5 pb-3">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <h3
            id={headingId}
            className="text-[13px] font-medium"
            style={{ color: skipped ? "var(--color-text-4)" : "var(--color-text)" }}
          >
            {t(`step_${step.id}`, { defaultValue: step.id })}
          </h3>
          <span className="text-[11.5px]" style={{ color: STEP_RAILS[step.state].tone.color }}>
            {t(`step_state_${step.state}`)}
          </span>
        </div>

        {skipped ? (
          <p className="text-[11.5px]" style={{ color: "var(--color-text-4)" }}>
            {t("step_skipped_hint")}
          </p>
        ) : (
          <>
            <ArtifactMeter collection={step.artifacts} />

            {narration && (
              <NarrationDeliveryChoice
                choice={narration.choice}
                ttsUnavailable={narration.ttsUnavailable}
                onSelect={narration.onSelect}
                busy={busy}
              />
            )}

            <StaleArtifacts
              staleIds={staleIds}
              onView={onViewUnit}
              onRegenerate={onRegenerate ? (ids) => onRegenerate(step.id, ids) : undefined}
              busy={busy}
            />

            <TaskChips tasks={step.tasks} />

            {step.problems.length > 0 && (
              <ProblemList
                problems={problemViews(t, step.problems, step.id)}
                labelledBy={headingId}
                className="space-y-1.5 text-[12px]"
              />
            )}

            {admission && admission.decision !== "admitted" && (
              <div className="space-y-1.5">
                <BatchAdmissionSummary admission={admission} />
                {admission.decision === "confirmation_required" && onConfirmDurations && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={confirmDurations}
                    className={INLINE_ACTION_CLS}
                    style={{ color: "var(--color-accent-2)" }}
                  >
                    {t("admission_confirm_cta")}
                  </button>
                )}
              </div>
            )}

            {step.action && step.action.type !== "none" && (
              <p className="text-[11.5px] leading-relaxed" style={{ color: "var(--color-text-3)" }}>
                {nextStepForAction(t, step.action.type)}
              </p>
            )}
          </>
        )}
      </div>
    </li>
  );
}
