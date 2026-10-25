import type { DraftSoftViolation, ScriptReviewViolation } from "@/types";
import type { TFunction } from "i18next";
import { itemPositionLabel } from "@/utils/episode-display";
import { episodeItemKind } from "@/utils/episode-item";

/** 草稿诊断按草稿下标称呼条目；提示词草稿可传正式剧本中的位置，避免按内部编号推算。 */
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

/** 草稿违约按条目分组：带条目定位的挂到对应条目，其余是整集层面的违约。 */
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
 * 按服务端下发的条目定位分组：优先取 `item_index`，只带 `item_id` 的按草稿里的条目 ID 找回下标。
 * 定位落在条目之外的违约（草稿已被改短、ID 已改名）归入整集层面，不会挂到不存在的条目上而消失。
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

/** 降级提示按条目分组；`excludeCodes` 中的提示由调用方按当前正文实时判，不取服务端快照。 */
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
