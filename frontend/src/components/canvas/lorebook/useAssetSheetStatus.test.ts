import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { AssetSheetStatusRow } from "@/types";
import { pendingSheetCounts, useAssetSheetStatus } from "./useAssetSheetStatus";

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


describe("pendingSheetCounts while generating", () => {
  it("leaves sheets that are already generating out of the batch count", () => {
    const rows = [
      row({ unit_id: "scene/庭院", name: "庭院" }),
      row({ unit_id: "scene/长街", name: "长街" }),
    ];

    expect(pendingSheetCounts(rows, "scene", (candidate) => candidate.name === "长街")).toEqual({
      generatable: 1,
      missingDescription: 0,
    });
  });
});

describe("useAssetSheetStatus", () => {
  it("does not carry one project's rows over to another while the new request is pending", async () => {
    const alpha = [row({ status: "current" })];
    vi.spyOn(API, "getAssetSheetStatus")
      .mockResolvedValueOnce({ assets: alpha })
      .mockReturnValueOnce(new Promise(() => {}));
    const { result, rerender } = renderHook(({ project }) => useAssetSheetStatus(project), {
      initialProps: { project: "alpha" },
    });
    await waitFor(() => expect(result.current).toEqual(alpha));

    rerender({ project: "beta" });

    expect(result.current).toEqual([]);
  });
});
