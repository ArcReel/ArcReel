import type { TFunction } from "i18next";
import type {
  AdmissionProblem,
  BatchAdmissionUnit,
  WorkflowActionType,
  WorkflowBlocker,
  WorkflowProblem,
} from "@/types/workflow";

/**
 * Shared panel problem presentation: per-unit whole-batch admission gaps,
 * structured plan problems and data-corruption blockers normalize into one UI-row shape.
 *
 * Four roles never substitute for each other: `unitId`/`field` identify **where**, `summary` explains
 * **why**, `nextStep` states **what to do next**, and `detail` preserves raw troubleshooting text.
 */
export interface ProblemView {
  key: string;
  /** Problem units; empty for project-level issues. */
  unitId?: string | null;
  /** Problem field paths, e.g. `generation_settings.audio_backend`. */
  field?: string | null;
  /** Localized one-sentence causes. */
  summary: string;
  /** Additional measures, e.g. tier comparisons. */
  meta?: string | null;
  /** Localized next-action statements; empty without corresponding copy, never invented. */
  nextStep?: string | null;
  /** Raw server text, collapsed outside summaries; plan hints have none. */
  detail?: string | null;
}

type Translate = TFunction<"workflow">;

/**
 * One-sentence causes localized by problem codes with `params`. Unknown codes use generic causes for returned actions;
 * unknown actions use broadest explanations. Summaries expose no raw text/problem codes/reason codes; raw text goes in collapsed details.
 */
function localizedSummary(
  t: Translate,
  code: string,
  action: string | null | undefined,
  params: Record<string, unknown> | undefined,
): string {
  const translated = t(`problem_${code}`, { ...params, defaultValue: "" });
  if (translated) return translated;
  return t(`problem_fallback_${action ?? "none"}`, { defaultValue: t("problem_fallback_unknown") });
}

/**
 * Repeat backend next actions. Action translations are bare imperatives; add Next step framing here,
 * never per caller. Types form the closed {@link WorkflowActionType} set, each with copy
 * (coverage in `action-language.test.ts`); `action_unknown` is runtime defense only for
 * actions deployed server-first, preferable to swallowing entire steps even without identifying specific actions.
 */
export function nextStepForAction(t: Translate, action: WorkflowActionType): string {
  return t("next_step", {
    step: t(`action_${action}`, { defaultValue: t("action_unknown") }),
  });
}

/** Problem-row next steps; empty without actions/corresponding translations, never invented. */
function nextStepFor(t: Translate, action: string | null | undefined): string | null {
  if (!action || action === "none") return null;
  const phrase = t(`action_${action}`, { defaultValue: "" });
  return phrase ? t("next_step", { step: phrase }) : null;
}

function stringParam(params: Record<string, unknown> | undefined, key: string): string | null {
  const value = params?.[key];
  return typeof value === "string" && value ? value : null;
}

function problemField(problem: AdmissionProblem): string | null {
  const field = stringParam(problem.params, "field") ?? stringParam(problem.params, "path");
  if (field) return field;
  const path = problem.params?.["path"];
  if (Array.isArray(path)) {
    const parts = path.filter((part): part is string => typeof part === "string");
    if (parts.length > 0) return parts.join(".");
  }
  return null;
}

/** Plan notices use `unit_id` and localized problem-code reasons without raw server text. */
export function problemViews(t: Translate, problems: WorkflowProblem[], keyPrefix = "problem"): ProblemView[] {
  return problems.map((problem, index) => ({
    key: `${keyPrefix}-${problem.code}-${index}`,
    unitId: problem.unit_id ?? null,
    summary: localizedSummary(t, problem.code, problem.action, problem.params),
    nextStep: nextStepFor(t, problem.action),
  }));
}

/**
 * Data-corruption blockers: `path` identifies user-repairable fields in summaries; `reason` contains
 * raw server text in collapsed regions. Show readable conclusions first, troubleshooting details only after expansion.
 */
export function blockerViews(t: Translate, blockers: WorkflowBlocker[]): ProblemView[] {
  return blockers.map((blocker, index) => ({
    key: `blocker-${blocker.code}-${index}`,
    field: blocker.path,
    summary: t(`problem_${blocker.code}`, { defaultValue: t("blocker_generic") }),
    nextStep: nextStepFor(t, "repair_project_data"),
    detail: blocker.reason,
  }));
}

/** Whole-batch admission markers for individually valid units not submitted with rejected batches. */
const WITHHELD_CODE = "generation_batch_admission_withheld";

export function isWithheld(unit: BatchAdmissionUnit): boolean {
  return unit.withheld === true || unit.problems.some((problem) => problem.code === WITHHELD_CODE);
}

/**
 * Unit admission gaps. Requested tiers appear beside causes; Duration exceeds limit alone conceals overage,
 * and users mainly rely on those figures to choose corrective edits.
 */
export function admissionUnitViews(
  t: Translate,
  units: BatchAdmissionUnit[],
  formatSeconds: (value: number) => string,
): ProblemView[] {
  const views: ProblemView[] = [];
  for (const unit of units) {
    const meta =
      unit.request_duration_seconds != null
        ? t("unit_request_tier", { request: formatSeconds(unit.request_duration_seconds) })
        : null;
    unit.problems.forEach((problem, index) => {
      views.push({
        key: `${unit.unit_id}-${problem.code}-${index}`,
        unitId: unit.unit_id,
        field: problemField(problem),
        // Batch endpoints localize message already; preserve raw text as collapsed technical details. Plan endpoints lack message,
        // using problem-code translation tables without displaying raw text.
        summary: problem.message ?? localizedSummary(t, problem.code, problem.action, problem.params),
        meta: index === 0 ? meta : null,
        nextStep: nextStepFor(t, problem.action),
        detail: problem.message ? (problem.detail ?? null) : null,
      });
    });
  }
  return views;
}

/**
 * Targets unreached during interrupted enqueue. Admission passed, so gaps are not unit-local and omit tier comparisons;
 * users need which targets missed queues and why, with tiers only adding noise.
 *
 * Parameters use structure rather than concrete types: generic problem normalization must not depend backward on route-specific receipts.
 * Server-localized `message` shares admission-gap shapes; `detail` is optional, and missing values
 * omit collapsible details from rows.
 */
export function enqueueFailureViews(
  t: Translate,
  failures: readonly { unit_id: string; problem: AdmissionProblem }[],
): ProblemView[] {
  return failures.map((failure, index) => ({
    key: `enqueue-${failure.unit_id}-${index}`,
    unitId: failure.unit_id,
    field: problemField(failure.problem),
    summary:
      failure.problem.message ??
      localizedSummary(t, failure.problem.code, failure.problem.action, failure.problem.params),
    nextStep: nextStepFor(t, failure.problem.action),
    detail: failure.problem.detail ?? null,
  }));
}
