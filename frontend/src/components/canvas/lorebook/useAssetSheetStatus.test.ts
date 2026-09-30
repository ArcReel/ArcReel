import { describe, expect, it } from "vitest";
import type { AssetSheetStatusRow } from "@/types";
import { pendingSheetCounts } from "./useAssetSheetStatus";

function row(overrides: Partial<AssetSheetStatusRow>): AssetSheetStatusRow {
  return {
    unit_id: "scene/庭院",
    asset_type: "scene",
    name: "庭院",
    derivative: null,
    status: "missing",
    description_missing: false,
    image_to_image: false,
    ...overrides,
  };
}

describe("pendingSheetCounts", () => {
  it("counts a derivative only when its owner sheet is usable or joins the same batch", () => {
    const rows = [
      row({ unit_id: "character/Alice", asset_type: "character", name: "Alice", status: "missing" }),
      row({ unit_id: "character/Alice/战损", asset_type: "character", name: "Alice", derivative: "战损" }),
      row({ unit_id: "character/Bob", asset_type: "character", name: "Bob", description_missing: true }),
      row({ unit_id: "character/Bob/雨夜", asset_type: "character", name: "Bob", derivative: "雨夜" }),
      row({ unit_id: "character/Cid", asset_type: "character", name: "Cid", status: "stale" }),
      row({ unit_id: "character/Cid/盛装", asset_type: "character", name: "Cid", derivative: "盛装" }),
    ];

    expect(pendingSheetCounts(rows, "character")).toEqual({ generatable: 3, missingDescription: 1 });
  });
});

