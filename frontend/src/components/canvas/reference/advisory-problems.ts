import type { ReferenceDurationPrecheck, ReferenceProjectionProblem } from "@/types";

/**
 * Preflight issues that do not block enqueueing but must be disclosed beforehand; currently only truncating references to the first N images.
 *
 * Centralize the decision: the gate uses it to open confirmation and the dialog to render its advisory section. Both ask
 * the same question; duplicated logic can silently diverge after changes, opening an empty dialog the user can neither confirm nor cancel.
 *
 * Skip entries without `message`: the server renders readable text in the current language, and without it there is nothing to display.
 */
export function advisoryProblems(
  precheck: ReferenceDurationPrecheck,
): ReferenceProjectionProblem[] {
  return precheck.problems.filter((problem) => !problem.blocking && Boolean(problem.message));
}
