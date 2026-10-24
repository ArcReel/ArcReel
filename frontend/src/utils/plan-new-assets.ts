import type { Character, NewAssetDecision, NewAssetType, PlanNewAsset, Prop, Scene } from "@/types";

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

/** 新增项类型对应的项目资产字典。 */
export const NEW_ASSET_BUCKET = { character: "characters", scene: "scenes", prop: "props" } as const;

/** 新增项的处理方式，按界面顺序。 */
export const NEW_ASSET_DECISIONS: readonly NewAssetDecision[] = ["register", "merge", "derivative", "skip"];

/**
 * 草稿里的 `new_assets` 可能被 Agent 改坏：缺键时视为没有新增项，形状或取值不对时返回 false，由调用方
 * 退回只读呈现。
 */
export function hasValidNewAssets(content: Record<string, unknown> | null): boolean {
  const items = content?.new_assets;
  if (items === undefined) return true;
  return (
    Array.isArray(items) &&
    items.every((item: unknown) => {
      if (item == null || typeof item !== "object") return false;
      const v = item as Record<string, unknown>;
      return (
        typeof v.name === "string" &&
        typeof v.type === "string" &&
        Object.hasOwn(NEW_ASSET_BUCKET, v.type) &&
        typeof v.decision === "string" &&
        (NEW_ASSET_DECISIONS as readonly string[]).includes(v.decision) &&
        typeof v.reason === "string" &&
        typeof v.description === "string" &&
        typeof v.target === "string" &&
        typeof v.asset_name === "string" &&
        Array.isArray(v.aliases)
      );
    })
  );
}


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
    const dict = result[NEW_ASSET_BUCKET[item.type]] as Record<string, { description: string }>;
    if (Object.hasOwn(registered[NEW_ASSET_BUCKET[item.type]], name)) continue;
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
