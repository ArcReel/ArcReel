import type { TFunction } from "i18next";
import type {
  AdmissionProblem,
  BatchAdmissionUnit,
  WorkflowActionType,
  WorkflowBlocker,
  WorkflowProblem,
} from "@/types/workflow";

export interface ProblemView {
  key: string;
  unitId?: string | null;
  field?: string | null;
  summary: string;
  meta?: string | null;
  nextStep?: string | null;
  detail?: string | null;
}

type Translate = TFunction<"workflow">;

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

export function nextStepForAction(t: Translate, action: WorkflowActionType): string {
  return t("next_step", {
    step: t(`action_${action}`, { defaultValue: t("action_unknown") }),
  });
}

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

export function problemViews(t: Translate, problems: WorkflowProblem[], keyPrefix = "problem"): ProblemView[] {
  return problems.map((problem, index) => ({
    key: `${keyPrefix}-${problem.code}-${index}`,
    unitId: problem.unit_id ?? null,
    summary: localizedSummary(t, problem.code, problem.action, problem.params),
    nextStep: nextStepFor(t, problem.action),
  }));
}

export function blockerViews(t: Translate, blockers: WorkflowBlocker[]): ProblemView[] {
  return blockers.map((blocker, index) => ({
    key: `blocker-${blocker.code}-${index}`,
    field: blocker.path,
    summary: t(`problem_${blocker.code}`, { defaultValue: t("blocker_generic") }),
    nextStep: nextStepFor(t, "repair_project_data"),
    detail: blocker.reason,
  }));
}

const WITHHELD_CODE = "generation_batch_admission_withheld";

export function isWithheld(unit: BatchAdmissionUnit): boolean {
  return unit.withheld === true || unit.problems.some((problem) => problem.code === WITHHELD_CODE);
}

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
        summary: problem.message ?? localizedSummary(t, problem.code, problem.action, problem.params),
        meta: index === 0 ? meta : null,
        nextStep: nextStepFor(t, problem.action),
        detail: problem.message ? (problem.detail ?? null) : null,
      });
    });
  }
  return views;
}

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
