import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { AssetCard } from "./AssetCard";

const asset = {
  id: "1", type: "scene" as const, name: "庙宇", description: "阴森古朴",
  voice_style: "", image_path: null, audio_path: null, source_project: "demo", updated_at: null,
  derivatives: [],
};

describe("AssetCard", () => {
  it("shows name + description", () => {
    render(<AssetCard asset={asset} onEdit={() => {}} onDelete={() => {}} />);
    expect(screen.getByText("庙宇")).toBeInTheDocument();
    expect(screen.getByText("阴森古朴")).toBeInTheDocument();
  });

  it("invokes onEdit on edit button click", () => {
    const onEdit = vi.fn();
    render(<AssetCard asset={asset} onEdit={onEdit} onDelete={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    expect(onEdit).toHaveBeenCalledWith(asset);
  });

  it("invokes onDelete on delete button click", () => {
    const onDelete = vi.fn();
    render(<AssetCard asset={asset} onEdit={() => {}} onDelete={onDelete} />);
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(onDelete).toHaveBeenCalledWith(asset);
  });

  it("shows the derivative count only when the character has derivatives", () => {
    const { rerender } = render(<AssetCard asset={asset} onEdit={() => {}} onDelete={() => {}} />);
    expect(screen.queryByTitle(/衍生（/)).not.toBeInTheDocument();

    rerender(
      <AssetCard
        asset={{
          ...asset,
          type: "character",
          derivatives: [
            { name: "战斗装", description: "黑甲", image_path: null },
            { name: "便装", description: "布衣", image_path: null },
          ],
        }}
        onEdit={() => {}}
        onDelete={() => {}}
      />,
    );
    expect(screen.getByTitle("衍生（2）")).toHaveTextContent("2");
  });
});
