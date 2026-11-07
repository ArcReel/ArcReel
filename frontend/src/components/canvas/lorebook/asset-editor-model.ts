import { API, type AssetCreateResult } from "@/api";
import type { AssetSheetType, Character, Product } from "@/types";
import type { GalleryAssetSource } from "./gallery-model";

/**
 * Unsaved detail-editor fields. Four asset types share shape, using subsets by type:
 * characters use voice style, products brand/selling points. Rename/upload/alias changes execute immediately outside this model.
 */
export interface AssetFields {
  description: string;
  voiceStyle: string;
  brand: string;
  /** Selling points, one per line. */
  sellingPoints: string;
}

/** Asset creation additionally has a name. */
export interface NewAssetFields extends AssetFields {
  name: string;
}

export const EMPTY_ASSET_FIELDS: AssetFields = { description: "", voiceStyle: "", brand: "", sellingPoints: "" };

export function assetFieldsOf(type: AssetSheetType, asset: GalleryAssetSource): AssetFields {
  return {
    description: asset.description ?? "",
    voiceStyle: type === "character" ? ((asset as Character).voice_style ?? "") : "",
    brand: type === "product" ? ((asset as Product).brand ?? "") : "",
    sellingPoints: type === "product" ? ((asset as Product).selling_points ?? []).join("\n") : "",
  };
}

/** Split selling-point text into lines, dropping blanks/trimming whitespace. */
export function parseSellingPoints(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** Compare split selling-point entries only; extra blank lines are not edits. */
export function assetFieldsEqual(a: AssetFields, b: AssetFields): boolean {
  return (
    a.description === b.description &&
    a.voiceStyle === b.voiceStyle &&
    a.brand === b.brand &&
    parseSellingPoints(a.sellingPoints).join("\n") === parseSellingPoints(b.sellingPoints).join("\n")
  );
}

/** Only changed fields, keyed by asset-API field names. */
export function assetFieldsPatch(type: AssetSheetType, value: AssetFields, saved: AssetFields): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  if (value.description !== saved.description) patch.description = value.description;
  if (type === "character" && value.voiceStyle !== saved.voiceStyle) patch.voice_style = value.voiceStyle;
  if (type === "product") {
    if (value.brand !== saved.brand) patch.brand = value.brand;
    const points = parseSellingPoints(value.sellingPoints);
    if (points.join("\n") !== parseSellingPoints(saved.sellingPoints).join("\n")) patch.selling_points = points;
  }
  return patch;
}

/** Canonical saved shape: remove blank selling-point lines, matching server writeback. */
export function normalizeAssetFields(value: AssetFields): AssetFields {
  return { ...value, sellingPoints: parseSellingPoints(value.sellingPoints).join("\n") };
}

export function updateAssetFields(
  projectName: string,
  type: AssetSheetType,
  name: string,
  patch: Record<string, unknown>,
): Promise<unknown> {
  switch (type) {
    case "character":
      return API.updateCharacter(projectName, name, patch);
    case "scene":
      return API.updateProjectScene(projectName, name, patch);
    case "prop":
      return API.updateProjectProp(projectName, name, patch);
    case "product":
      return API.updateProjectProduct(projectName, name, patch);
  }
}

export function createAsset(
  projectName: string,
  type: AssetSheetType,
  fields: NewAssetFields,
): Promise<AssetCreateResult> {
  const name = fields.name.trim();
  switch (type) {
    case "character":
      return API.addCharacter(projectName, name, fields.description, fields.voiceStyle);
    case "scene":
      return API.addProjectScene(projectName, name, fields.description);
    case "prop":
      return API.addProjectProp(projectName, name, fields.description);
    case "product":
      return API.addProjectProduct(
        projectName,
        name,
        fields.description,
        fields.brand.trim() || undefined,
        parseSellingPoints(fields.sellingPoints),
      );
  }
}
