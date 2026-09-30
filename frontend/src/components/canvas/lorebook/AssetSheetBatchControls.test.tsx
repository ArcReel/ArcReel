import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { AssetSheetStatusRow } from "@/types";
import { AssetSheetBatchControls } from "./AssetSheetBatchControls";

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

describe("AssetSheetBatchControls", () => {
  it("offers the type batch with its count and notes the ones lacking a description", () => {
    const onFilterChange = vi.fn();
    render(
      <AssetSheetBatchControls
        projectName="demo"
        assetType="scene"
        rows={[
          row({ unit_id: "scene/庭院", name: "庭院" }),
          row({ unit_id: "scene/书房", name: "书房" }),
          row({ unit_id: "scene/阁楼", name: "阁楼", description_missing: true }),
          row({ unit_id: "scene/卧室", name: "卧室", status: "current" }),
        ]}
        filter="all"
        onFilterChange={onFilterChange}
      />,
    );

    expect(screen.getByRole("button", { name: /生成待生成的场景（2）/ })).toBeInTheDocument();
    expect(screen.getByText("另有 1 个缺描述")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "过期" }));
    expect(onFilterChange).toHaveBeenCalledWith("stale");
  });
});

