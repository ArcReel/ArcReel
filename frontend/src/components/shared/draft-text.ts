import type { DraftDocType, ScriptReviewViolation } from "@/types";

/**
 * Prefilled text when handing a repairable draft to the Agent: list violations, or ask for direct adoption when none remain.
 * `episodeRef` identifies the episode by name and ID (`episodeAgentRef`).
 */
export function draftFixRequestText(
  t: (key: string, options?: Record<string, unknown>) => string,
  episodeRef: string,
  docType: DraftDocType,
  violations: ScriptReviewViolation[],
): string {
  const draftName = t(
    docType === "reference_prompt_authoring" ? "dashboard:draft_name_prompt_authoring" : "dashboard:draft_name_script_plan",
  );
  if (violations.length === 0) {
    return t("dashboard:draft_fix_request_promote_prefill", { episodeRef, docType, draftName });
  }
  return [
    t("dashboard:draft_fix_request_prefill_header", { episodeRef, count: violations.length, docType, draftName }),
    ...violations.map((v, i) => `${i + 1}. ${v.message}`),
  ].join("\n");
}

/** Explain which content discarding returns to, according to the draft's document and whether formal content exists. */
export function draftFallbackText(
  t: (key: string, options?: Record<string, unknown>) => string,
  docType: DraftDocType,
  formalExists: boolean,
): string {
  if (docType === "reference_prompt_authoring") return t("dashboard:draft_discard_to_formal_script");
  return formalExists
    ? t("dashboard:draft_discard_to_formal_script_plan")
    : t("dashboard:draft_discard_to_empty_script_plan");
}
