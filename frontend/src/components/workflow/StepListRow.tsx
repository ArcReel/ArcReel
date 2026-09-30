import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { WorkflowPlanStep } from "@/types/workflow";
import { BatchAdmissionSummary } from "./BatchAdmissionSummary";
import { ProblemList } from "./ProblemList";
import { StaleArtifacts } from "./StaleArtifacts";
import { TaskChips } from "./TaskChips";
import { StepActButton } from "./StepActButton";
import { INLINE_ACTION_CLS } from "./state-language";
import { problemViews } from "./problem-views";
import type { NextStepView, StepAct, StepNote, StepRowTone, StepRowView } from "./step-list";

const TONE_COLOR: Record<StepRowTone, string> = {
  done: "var(--color-accent-2)",
  todo: "var(--color-text-4)",
  partial: "var(--color-text-2)",
  running: "var(--color-accent-2)",
  warn: "var(--color-warm)",
  danger: "var(--color-danger-2)",
};

const NOTE_COLOR: Record<StepNote["tone"], string> = {
  warn: "var(--color-warm)",
  danger: "var(--color-danger-2)",
  info: "var(--color-text-3)",
};

/** 行首圆点：已齐实心、还没有虚线空心、部分半填、进行中脉动。形状先于颜色，灰度下仍可区分。 */
function ToneDot({ tone }: { tone: StepRowTone }) {
  const color = TONE_COLOR[tone];
  const base = "inline-block h-2 w-2 shrink-0 rounded-full";
  if (tone === "todo") return <span aria-hidden className={base} style={{ border: `1px dashed ${color}` }} />;
  if (tone === "partial") {
    return (
      <span
        aria-hidden
        className={base}
        style={{ background: `linear-gradient(90deg, ${color} 50%, transparent 50%)`, border: `1px solid ${color}` }}
      />
    );
  }
  if (tone === "running") return <span aria-hidden className={`${base} motion-safe:animate-pulse`} style={{ background: color }} />;
  return <span aria-hidden className={base} style={{ background: color }} />;
}

function NoteLine({ note, onRun }: { note: StepNote; onRun: (act: StepAct) => void }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 text-[11.5px]" style={{ color: NOTE_COLOR[note.tone] }}>
      <span>{note.text}</span>
      {note.act && <StepActButton act={note.act} onRun={onRun} size="sm" asLink />}
    </div>
  );
}

interface NextProps {
  next: NextStepView;
  instruction: string;
  onInstructionChange: (value: string) => void;
  onRun: (act: StepAct) => void;
  busy: boolean;
}

/** 就地展开的下一步：说明、附加指令、主次入口、「或者 …」。 */
function NextStepBlock({ next, instruction, onInstructionChange, onRun, busy }: NextProps) {
  const { t } = useTranslation("workflow");
  const inputId = useId();
  return (
    <div className="space-y-1.5 pt-1" data-testid="workflow-next-step">
      <p className="text-[12px] leading-relaxed">
        <span className="font-medium" style={{ color: "var(--color-accent-2)" }}>
          {t("next_step", { step: next.title })}
        </span>
        {next.detail && <span style={{ color: "var(--color-text-3)" }}> {next.detail}</span>}
      </p>
      {next.hint && <NoteLine note={next.hint} onRun={onRun} />}
      {next.instruction && (
        <div className="max-w-[440px]">
          <label htmlFor={inputId} className="sr-only">
            {t(next.instruction.persist ? "instruction_label_saved" : "instruction_label")}
          </label>
          <input
            id={inputId}
            value={instruction}
            onChange={(event) => onInstructionChange(event.target.value)}
            placeholder={t(next.instruction.persist ? "instruction_placeholder_saved" : "instruction_placeholder")}
            className="focus-ring w-full rounded-md px-2 py-1 text-[12px]"
            style={{
              background: "var(--color-surface-2)",
              border: "1px solid var(--color-hairline)",
              color: "var(--color-text)",
            }}
          />
        </div>
      )}
      {(next.primary.length > 0 || next.alternatives.length > 0) && (
        <div className="flex flex-wrap items-center gap-2">
          {next.primary.map((act) => (
            <StepActButton key={act.key} act={act} onRun={onRun} busy={busy} />
          ))}
          {next.alternatives.length > 0 && (
            <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="text-[11.5px]" style={{ color: "var(--color-text-4)" }}>
                {t("alternatives_lead")}
              </span>
              {next.alternatives.map((act) => (
                <StepActButton key={act.key} act={act} onRun={onRun} size="sm" asLink busy={busy} />
              ))}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

interface Props {
  row: StepRowView;
  next: NextStepView | null;
  instruction: string;
  onInstructionChange: (value: string) => void;
  onRun: (act: StepAct) => void;
  /** 跳到画布上的该单元。 */
  onViewUnit?: (unitId: string) => void;
  /** 显式重生这一步的指定单元（分镜图、视频）。 */
  onRegenerate?: (stepId: string, unitIds: string[]) => void;
  onConfirmDurations?: (durations: Record<string, number>) => void;
  busy: boolean;
}

/** 分镜图与视频的过期产物可以就地查看与重生；资产图的过期只作提醒并跳到画廊。 */
const REGENERABLE_STEPS = new Set(["storyboard", "video"]);

function StepDetails({ step, onViewUnit, onRegenerate, onConfirmDurations, busy }: {
  step: WorkflowPlanStep;
  onViewUnit?: (unitId: string) => void;
  onRegenerate?: (stepId: string, unitIds: string[]) => void;
  onConfirmDurations?: (durations: Record<string, number>) => void;
  busy: boolean;
}) {
  const { t } = useTranslation("workflow");
  const staleIds = REGENERABLE_STEPS.has(step.id) && Array.isArray(step.artifacts.stale_ids) ? step.artifacts.stale_ids : [];
  const admission = step.admission;
  const tiers = admission?.confirmation?.tiers ?? [];
  const confirm = () => {
    const durations: Record<string, number> = {};
    for (const tier of tiers) {
      if (tier.request_duration_seconds == null) continue;
      for (const unitId of tier.unit_ids) durations[unitId] = tier.request_duration_seconds;
    }
    onConfirmDurations?.(durations);
  };
  return (
    <>
      <StaleArtifacts
        staleIds={staleIds}
        onView={onViewUnit}
        onRegenerate={onRegenerate ? (unitIds) => onRegenerate(step.id, unitIds) : undefined}
        busy={busy}
      />
      <TaskChips tasks={step.tasks} />
      {step.problems.length > 0 && (
        <ProblemList problems={problemViews(t, step.problems, step.id)} className="space-y-1.5 text-[12px]" />
      )}
      {admission && admission.decision !== "admitted" && (
        <div className="space-y-1.5">
          <BatchAdmissionSummary admission={admission} />
          {admission.decision === "confirmation_required" && onConfirmDurations && (
            <button
              type="button"
              disabled={busy}
              onClick={confirm}
              className={INLINE_ACTION_CLS}
              style={{ color: "var(--color-accent-2)" }}
            >
              {t("admission_confirm_cta")}
            </button>
          )}
        </div>
      )}
    </>
  );
}

/** 一行内容：现状一句话、提醒、常驻入口；下一步属于这一行时就地展开。 */
export function StepListRow({ row, next, instruction, onInstructionChange, onRun, onViewUnit, onRegenerate, onConfirmDurations, busy }: Props) {
  const owns = next !== null;
  return (
    <li
      className="flex gap-2.5 rounded-md py-1 pl-1.5 pr-2"
      data-testid={`workflow-row-${row.key}`}
      aria-current={owns ? "step" : undefined}
      style={owns ? { background: "var(--color-accent-dim)", boxShadow: "inset 2px 0 0 var(--color-accent-2)" } : undefined}
    >
      <span className="mt-[6px] flex">
        <ToneDot tone={row.tone} />
      </span>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <h3 className="w-24 shrink-0 text-[12.5px] font-medium" style={{ color: "var(--color-text)" }}>
            {row.title}
          </h3>
          <span
            className="text-[12px]"
            style={{ color: row.tone === "warn" || row.tone === "danger" ? TONE_COLOR[row.tone] : "var(--color-text-2)" }}
          >
            {row.status}
          </span>
        </div>
        <div className="space-y-1 sm:pl-[104px]">
          {row.notes.map((note) => (
            <NoteLine key={note.key} note={note} onRun={onRun} />
          ))}
          {row.acts.length > 0 && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {row.acts.map((act) => (
                <StepActButton key={act.key} act={act} onRun={onRun} size="sm" asLink busy={busy} />
              ))}
            </div>
          )}
          {row.steps.map((step) => (
            <StepDetails
              key={step.id}
              step={step}
              onViewUnit={onViewUnit}
              onRegenerate={onRegenerate}
              onConfirmDurations={onConfirmDurations}
              busy={busy}
            />
          ))}
          {next && (
            <NextStepBlock
              next={next}
              instruction={instruction}
              onInstructionChange={onInstructionChange}
              onRun={onRun}
              busy={busy}
            />
          )}
        </div>
      </div>
    </li>
  );
}
