import type { Character, NewAssetType, PlanNewAsset, Prop, Scene } from "@/types";

export type NamesByType = Record<NewAssetType, Set<string>>;

export interface PlanAssetDicts {
  characters: Record<string, Character>;
  scenes: Record<string, Scene>;
  props: Record<string, Prop>;
}

export interface PlanReferenceCandidates extends PlanAssetDicts {
  /** 本集新增、确认时登记的名字（与已登记资产同名、会自动归并的项不在其列）。 */
  newNames: NamesByType;
  /** 本集新增里选了「不登记」的名字。 */
  skippedNames: NamesByType;
}

const BUCKET = { character: "characters", scene: "scenes", prop: "props" } as const;

function emptyNames(): NamesByType {
  return { character: new Set(), scene: new Set(), prop: new Set() };
}

/**
 * 内容确认时引用的可选范围：已登记资产加本集新增项中不是「不登记」的项。新增项以称呼叠加到同类
 * 资产字典上，描述取它的登记描述；「不登记」的项不作候选，单独列出。
 */
export function planReferenceCandidates(
  registered: PlanAssetDicts,
  items: readonly PlanNewAsset[],
): PlanReferenceCandidates {
  const result: PlanReferenceCandidates = {
    characters: { ...registered.characters },
    scenes: { ...registered.scenes },
    props: { ...registered.props },
    newNames: emptyNames(),
    skippedNames: emptyNames(),
  };
  for (const item of items) {
    const name = item.name.trim();
    if (!name) continue;
    const dict = result[BUCKET[item.type]] as Record<string, { description: string }>;
    if (Object.hasOwn(registered[BUCKET[item.type]], name)) continue;
    if (item.decision === "skip") {
      result.skippedNames[item.type].add(name);
      continue;
    }
    if (!Object.hasOwn(dict, name)) dict[name] = { description: item.description };
    result.newNames[item.type].add(name);
  }
  return result;
}

/** 台词说话人的候选：已登记角色加本集新增角色（不含「不登记」的项）。说话人仍可以写其他名字。 */
export function speakerCandidates(
  characters: Record<string, Character>,
  items: readonly PlanNewAsset[] = [],
): string[] {
  const names = new Set(Object.keys(characters));
  for (const item of items) {
    const name = item.name.trim();
    if (item.type === "character" && item.decision !== "skip" && name) names.add(name);
  }
  return [...names];
}
