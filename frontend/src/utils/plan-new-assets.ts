import type { Character, NewAssetDecision, NewAssetType, PlanNewAsset, Prop, Scene } from "@/types";

export type NamesByType = Record<NewAssetType, Set<string>>;

export interface PlanAssetDicts {
  characters: Record<string, Character>;
  scenes: Record<string, Scene>;
  props: Record<string, Prop>;
}

export interface PlanReferenceCandidates extends PlanAssetDicts {
  /** New episode names registered on confirmation, excluding same-name registered assets automatically merged. */
  newNames: NamesByType;
  /** New names selected as Do not register. */
  skippedNames: NamesByType;
}

/** Project dictionaries for new-asset kinds. */
export const NEW_ASSET_BUCKET = { character: "characters", scene: "scenes", prop: "props" } as const;

/** New-item dispositions in UI order. */
export const NEW_ASSET_DECISIONS: readonly NewAssetDecision[] = ["register", "merge", "derivative", "skip"];

/**
 * Agents may corrupt draft `new_assets`: missing keys mean no new assets; invalid shapes/values return false, letting callers
 * fall back to readonly presentation.
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
 * Content-confirmation references include registered assets plus new episode items not marked Do not register. Overlay new names onto same-kind
 * dictionaries with registration descriptions; unregistered selections are separate, never candidates.
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

/** Speaker candidates: registered characters plus new registerable characters; other names remain permitted. */
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
