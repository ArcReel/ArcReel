import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { AssetCard } from "./AssetCard";

vi.mock("react-i18next", async () => {
  const { reactI18nextMock } = await import("@/test/mocks/reactI18next");
  return reactI18nextMock();
});

const asset = {
  id: "1", type: "scene" as const, name: "庙宇", description: "阴森古朴",
  voice_style: "", image_path: null, audio_path: null, source_project: "demo", updated_at: null,
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
    fireEvent.click(screen.getByRole("button", { name: /edit/ }));
    expect(onEdit).toHaveBeenCalledWith(asset);
  });

  it("invokes onDelete on delete button click", () => {
    const onDelete = vi.fn();
    render(<AssetCard asset={asset} onEdit={() => {}} onDelete={onDelete} />);
    fireEvent.click(screen.getByRole("button", { name: /delete/ }));
    expect(onDelete).toHaveBeenCalledWith(asset);
  });
});
