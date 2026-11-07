import type { DraftSoftViolation, ScriptReviewViolation } from "@/types";
import type { TFunction } from "i18next";
import { itemPositionLabel } from "@/utils/episode-display";
import { episodeItemKind } from "@/utils/episode-item";

/** Draft diagnostics name entries by draft index; prompt drafts may supply formal-script positions to avoid inferring from internal IDs. */
export function draftViolationMessage(
  violation: Pick<ScriptReviewViolation, "message" | "label" | "item_id" | "item_index"> | DraftSoftViolation,
  t: TFunction,
  position?: number | null,
): string {
  const current = position === undefined ? (violation.item_index == null ? null : violation.item_index + 1) : position;
  const label = ("label" in violation ? violation.label : undefined) || violation.item_id;
  if (!label || (current === null && position === undefined)) return violation.message;
  const itemId = violation.item_id ?? /E\d+[SU]\d+(?:_\d+)?/.exec(label)?.[0];
  const kind = itemId ? episodeItemKind(itemId) : label.startsWith("unit ") ? "unit" : "shot";
  return violation.message.replace(label, itemPositionLabel(kind, current, t));
}

/** Group draft violations by entry: attach positioned ones to entries and all others to the episode. */
export interface DraftViolationGroups {
  episodeLevel: ScriptReviewViolation[];
  byItem: Map<number, ScriptReviewViolation[]>;
}

function pushTo<V>(map: Map<number, V[]>, index: number, value: V): void {
  const list = map.get(index) ?? [];
  list.push(value);
  map.set(index, list);
}

/**
 * Group by server-provided entry positions: prefer `item_index`; if only `item_id` exists, recover its index from draft IDs.
 * Violations pointing outside entries, after draft shortening or ID renaming, stay at episode level instead of disappearing on nonexistent entries.
 */
export function groupDraftViolations(
  violations: ScriptReviewViolation[],
  itemIds: readonly string[],
): DraftViolationGroups {
  const episodeLevel: ScriptReviewViolation[] = [];
  const byItem = new Map<number, ScriptReviewViolation[]>();
  for (const violation of violations) {
    const index =
      violation.item_index ?? (violation.item_id != null ? itemIds.indexOf(violation.item_id) : -1);
    if (index < 0 || index >= itemIds.length) episodeLevel.push(violation);
    else pushTo(byItem, index, violation);
  }
  return { episodeLevel, byItem };
}

/** Group downgrade hints by entry; callers derive `excludeCodes` hints live from current body text instead of server snapshots. */
export function groupSoftViolations(
  softViolations: DraftSoftViolation[],
  excludeCodes: ReadonlySet<string> = new Set(),
): Map<number, DraftSoftViolation[]> {
  const byItem = new Map<number, DraftSoftViolation[]>();
  for (const soft of softViolations) {
    if (!excludeCodes.has(soft.code)) pushTo(byItem, soft.item_index, soft);
  }
  return byItem;
}
