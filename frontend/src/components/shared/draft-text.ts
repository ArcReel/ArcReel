import type { DraftDocType, ScriptReviewViolation } from "@/types";

/**
 * 待修复草稿交给 Agent 时的预填：列出违约，或在违约已清零时请它直接采用。
 * `episodeRef` 是集名连同集 ID 的指称（`episodeAgentRef`）。
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

/** 丢弃后回到哪份内容：按草稿对应的文档与正式内容是否存在给出说明。 */
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
