import type { TFunction } from "i18next";
import type { ReferenceVideoUnit, TaskItem } from "@/types";
import {
  itemIdsAsPositionsInText,
  itemPositionLabel,
  type ProjectScripts,
} from "@/utils/episode-display";
import { findEpisodeItem } from "@/utils/episode-item";

export interface ReferenceAuthoringFailedUnit {
  unit_id: string;
  code: string;
  message: string;
  abort_reason?: string;
}

export interface ReferenceAuthoringPartialResult {
  taskId: string;
  rewrite: boolean;
  instructions: string | null;
  writtenIds: string[];
  failedUnits: ReferenceAuthoringFailedUnit[];
}

const AGENT_RETRY_SUFFIX =
  /[；;]\s*请用\s*entry_ids\s*点名重试（重写需重新认可覆盖），或用\s*patch_episode_script\s*修改\s*$/;
const LEADING_CONFLICT_CODE = /^formal_revision_conflict:\s*/i;
const UNIT_LABEL_RE = /(?:unit\s+)?(?<![A-Za-z0-9])(E\d+U\d+(?:_\d+)?)(?![A-Za-z0-9])/g;

function parseFailedUnit(raw: unknown): ReferenceAuthoringFailedUnit | null {
  if (!raw || typeof raw !== "object") return null;
  const record = raw as Record<string, unknown>;
  const unitId = typeof record.unit_id === "string" ? record.unit_id.trim() : "";
  if (!unitId) return null;
  const code = typeof record.code === "string" ? record.code : "";
  const message = typeof record.message === "string" ? record.message.trim() : "";
  const abortReason =
    typeof record.abort_reason === "string" && record.abort_reason.trim()
      ? record.abort_reason.trim()
      : undefined;
  return abortReason
    ? { unit_id: unitId, code, message, abort_reason: abortReason }
    : { unit_id: unitId, code, message };
}

export function parseReferenceAuthoringPartial(
  task: TaskItem | null | undefined,
): ReferenceAuthoringPartialResult | null {
  if (!task || task.task_type !== "text_episode_script" || task.status !== "succeeded") return null;
  const result = task.result;
  if (!result || result.outcome !== "partial") return null;
  if (!Array.isArray(result.failed_units)) return null;
  const failedUnits = result.failed_units
    .map(parseFailedUnit)
    .filter((item): item is ReferenceAuthoringFailedUnit => item !== null);
  if (failedUnits.length === 0) return null;
  const writtenIds = Array.isArray(result.written_ids)
    ? result.written_ids.filter((id): id is string => typeof id === "string" && id.trim().length > 0)
    : [];
  const rawInstructions = task.payload?.instructions;
  const instructions =
    typeof rawInstructions === "string" && rawInstructions.trim() ? rawInstructions.trim() : null;
  return {
    taskId: task.task_id,
    rewrite: task.payload?.rewrite === true,
    instructions,
    writtenIds,
    failedUnits,
  };
}

export function referenceUnitPosition(
  unitId: string,
  units: readonly Pick<ReferenceVideoUnit, "unit_id">[],
  scripts: ProjectScripts,
): number | null {
  const index = units.findIndex((unit) => unit.unit_id === unitId);
  if (index >= 0) return index + 1;
  return findEpisodeItem(scripts, unitId)?.position ?? null;
}

export function formatReferenceUnitText(
  text: string,
  units: readonly Pick<ReferenceVideoUnit, "unit_id">[],
  scripts: ProjectScripts,
  t: TFunction,
): string {
  const replaced = text.replace(UNIT_LABEL_RE, (_match, unitId: string) =>
    itemPositionLabel("unit", referenceUnitPosition(unitId, units, scripts), t),
  );
  return itemIdsAsPositionsInText(replaced, scripts, t);
}

export function formatPartialFailureMessage(
  message: string,
  units: readonly Pick<ReferenceVideoUnit, "unit_id">[],
  scripts: ProjectScripts,
  t: TFunction,
): string {
  const cleaned = message.replace(AGENT_RETRY_SUFFIX, "").trim();
  return formatReferenceUnitText(cleaned, units, scripts, t);
}

export function formatPartialAbortReason(
  reason: string | undefined,
  units: readonly Pick<ReferenceVideoUnit, "unit_id">[],
  scripts: ProjectScripts,
  t: TFunction,
): string | null {
  if (!reason) return null;
  const cleaned = reason.replace(LEADING_CONFLICT_CODE, "").trim();
  if (!cleaned) return null;
  return formatReferenceUnitText(cleaned, units, scripts, t);
}

const failureKeys: Record<string, string> = {
  missing_unit: "prompt_partial_missing",
  duplicate_unit: "prompt_partial_duplicate",
  dialogue_mismatch: "prompt_partial_dialogue",
  dialogue_rewritten: "prompt_partial_dialogue",
  dialogue_line_count_changed: "prompt_partial_dialogue",
  duration_off_tier: "prompt_partial_duration",
  formal_revision_conflict: "prompt_partial_conflict",
  speakers_over_limit: "prompt_partial_speakers",
};

export function partialFailureSummary(item: ReferenceAuthoringFailedUnit, t: TFunction): string {
  return t(`dashboard:${failureKeys[item.code] ?? "prompt_partial_invalid"}`);
}

export function partialAbortSummary(reason: string, t: TFunction): string {
  if (LEADING_CONFLICT_CODE.test(reason)) return t("dashboard:prompt_partial_conflict");
  if (reason === "视频模型能力不可用") return t("dashboard:prompt_partial_capability");
  return t("dashboard:prompt_partial_stopped");
}

export function buildPartialAuthoringAgentPrefill(
  t: TFunction,
  options: {
    episodeRef: string;
    units: readonly ReferenceVideoUnit[];
    scripts: ProjectScripts;
    failedUnits: readonly ReferenceAuthoringFailedUnit[];
    rewriteMode: boolean;
  },
): string {
  const { episodeRef, units, scripts, failedUnits, rewriteMode } = options;
  const lines = [
    t("dashboard:prompt_authoring_partial_agent_prefill_header", { episodeRef }),
    ...failedUnits.map((item, index) => {
      const pos = referenceUnitPosition(item.unit_id, units, scripts);
      const label = itemPositionLabel("unit", pos, t);
      const ref = t("common:item_agent_ref", { label, id: item.unit_id });
      const reason = partialFailureSummary(item, t);
      const abort = item.abort_reason && partialAbortSummary(item.abort_reason, t);
      const detail = abort
        ? `${reason}（${t("dashboard:prompt_authoring_partial_abort_reason", { reason: abort })}）`
        : reason;
      return `${index + 1}. ${ref}: ${detail}`;
    }),
    t(
      rewriteMode
        ? "dashboard:prompt_authoring_partial_agent_prefill_rewrite_footer"
        : "dashboard:prompt_authoring_partial_agent_prefill_fill_footer",
    ),
  ];
  return lines.join("\n");
}
