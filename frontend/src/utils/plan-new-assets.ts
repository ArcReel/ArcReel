import type { Character, NewAssetDecision, NewAssetType, PlanNewAsset, Prop, Scene } from "@/types";

export type NamesByType = Record<NewAssetType, Set<string>>;

export interface PlanAssetDicts {
  characters: Record<string, Character>;
  scenes: Record<string, Scene>;
  props: Record<string, Prop>;
}

export interface PlanReferenceCandidates extends PlanAssetDicts {
  newNames: NamesByType;
  skippedNames: NamesByType;
}

export const NEW_ASSET_BUCKET = { character: "characters", scene: "scenes", prop: "props" } as const;

export const NEW_ASSET_DECISIONS: readonly NewAssetDecision[] = ["register", "merge", "derivative", "skip"];

function emptyNames(): NamesByType {
  return { character: new Set(), scene: new Set(), prop: new Set() };
}

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
