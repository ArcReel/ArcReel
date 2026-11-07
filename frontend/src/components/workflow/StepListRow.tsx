import { useId } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { WorkflowPlanStep } from "@/types/workflow";
import { BatchAdmissionSummary } from "./BatchAdmissionSummary";
import { ProblemList } from "./ProblemList";
import { StaleArtifacts } from "./StaleArtifacts";
import { TaskChips } from "./TaskChips";
import { StepActButton } from "./StepActButton";
import { problemViews } from "./problem-views";
import type { NextStepView, StepAct, StepNote, StepRowTone, StepRowView } from "./step-list";

/** Leading dots: complete solid, absent dashed hollow, partial half-filled, running breathing. Shapes precede colors and survive grayscale. */
function ToneDot({ tone }: { tone: StepRowTone }) {
  if (tone === "todo") return <span aria-hidden className="size-2 rounded-full border border-dashed border-muted-foreground" />;
  if (tone === "partial") {
    return (
      <span aria-hidden className="relative size-2 overflow-hidden rounded-full border border-subtle-foreground">
        <span className="absolute inset-y-0 left-0 w-1/2 bg-subtle-foreground" />
      </span>
    );
  }
  return (
    <span
      aria-hidden
      className={cn(
        "size-2 rounded-full",
        tone === "warn" ? "bg-warn" : tone === "danger" ? "bg-destructive" : "bg-primary",
        tone === "running" && "animate-breath",
      )}
    />
  );
}

function NoteLine({ note, onRun }: { note: StepNote; onRun: (act: StepAct) => void }) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-baseline gap-x-2 text-xs",
        note.tone === "warn" ? "text-warn" : note.tone === "danger" ? "text-destructive" : "text-subtle-foreground",
      )}
    >
      <span>{note.text}</span>
      {note.act && <StepActButton act={note.act} onRun={onRun} asLink />}
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

/**
 * Inline-expanded next steps: explanations, extra instructions, primary/secondary entries and Or alternatives. Accent fills cover only these blocks;
 * all secondary text uses subtle because muted atop popover/accent fills falls below 4.5:1.
 */
function NextStepBlock({ next, instruction, onInstructionChange, onRun, busy }: NextProps) {
  const { t } = useTranslation("workflow");
  const inputId = useId();
  return (
    <div className="flex flex-col gap-2 rounded-md bg-primary/10 p-2" data-testid="workflow-next-step">
      <p className="text-xs leading-relaxed">
        <span className="font-medium text-primary">{t("next_step", { step: next.title })}</span>
        {next.detail && <span className="text-subtle-foreground"> {next.detail}</span>}
      </p>
      {next.hint && <NoteLine note={next.hint} onRun={onRun} />}
      {next.instruction && (
        <div className="max-w-110">
          <label htmlFor={inputId} className="sr-only">
            {t(next.instruction.persist ? "instruction_label_saved" : "instruction_label")}
          </label>
          <Input
            id={inputId}
            value={instruction}
            onChange={(event) => onInstructionChange(event.target.value)}
            placeholder={t(next.instruction.persist ? "instruction_placeholder_saved" : "instruction_placeholder")}
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
              <span className="text-xs text-subtle-foreground">{t("alternatives_lead")}</span>
              {next.alternatives.map((act) => (
                <StepActButton key={act.key} act={act} onRun={onRun} asLink busy={busy} />
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
  /** Navigate to canvas units. */
  onViewUnit?: (unitId: string) => void;
  /** Explicitly regenerate specified storyboard/video units for steps. */
  onRegenerate?: (stepId: string, unitIds: string[]) => void;
  onConfirmDurations?: (durations: Record<string, number>) => void;
  busy: boolean;
}

/** Stale storyboard/videos allow inline inspection/regeneration; stale asset images only remind and link to galleries. */
const REGENERABLE_STEPS = new Set(["storyboard", "video"]);

/**
 * Backend step details assigned to rows: stale artifacts, pending tasks, step-local problems and whole-video-batch admission.
 * Each hint has one source; unit-level video admission issues occur only in admission conclusions, never duplicated into step problems.
 */
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
      <ProblemList problems={problemViews(t, step.problems, step.id)} />
      {admission && admission.decision !== "admitted" && (
        <div className="flex flex-col gap-1.5">
          <BatchAdmissionSummary admission={admission} className="text-xs" />
          {admission.decision === "confirmation_required" && onConfirmDurations && (
            <div>
              <Button variant="outline" size="xs" disabled={busy} onClick={confirm}>
                {t("admission_confirm_cta")}
              </Button>
            </div>
          )}
        </div>
      )}
    </>
  );
}

/** Rows contain status sentences, reminders and persistent entries; belonging next steps expand inline with leading accent bars. */
export function StepListRow({ row, next, instruction, onInstructionChange, onRun, onViewUnit, onRegenerate, onConfirmDurations, busy }: Props) {
  const owns = next !== null;
  return (
    <li
      className={cn(
        "grid grid-cols-[0.5rem_6rem_minmax(0,1fr)] items-baseline gap-x-2.5 gap-y-1 border-l-2 py-1.5 pr-2 pl-1.5",
        owns ? "border-primary" : "border-transparent",
      )}
      data-testid={`workflow-row-${row.key}`}
      aria-current={owns ? "step" : undefined}
    >
      <span className="flex self-center">
        <ToneDot tone={row.tone} />
      </span>
      <h3 className="text-sm font-medium text-foreground">{row.title}</h3>
      <span
        className={cn(
          "text-xs",
          row.tone === "warn" ? "text-warn" : row.tone === "danger" ? "text-destructive" : "text-subtle-foreground",
        )}
      >
        {row.status}
      </span>
      <div className="col-span-2 col-start-2 flex min-w-0 flex-col gap-1.5 empty:hidden">
        {row.notes.map((note) => (
          <NoteLine key={note.key} note={note} onRun={onRun} />
        ))}
        {row.acts.length > 0 && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {row.acts.map((act) => (
              <StepActButton key={act.key} act={act} onRun={onRun} asLink busy={busy} />
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
    </li>
  );
}
