import type { DraftSoftViolation, ScriptReviewViolation } from "@/types";

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
