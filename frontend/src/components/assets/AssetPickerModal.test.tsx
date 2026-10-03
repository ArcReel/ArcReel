import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { Asset } from "@/types/asset";
import { AssetPickerModal } from "./AssetPickerModal";

function makeAsset(id: string, name: string): Asset {
  return {
    id,
    type: "character",
    name,
    description: "",
    voice_style: "",
    image_path: null,
    audio_path: null,
    source_project: null,
    updated_at: null,
    derivatives: [],
  };
}

const ITEMS = [makeAsset("1", "王小明"), makeAsset("2", "小师妹"), makeAsset("3", "李小红")];

describe("AssetPickerModal", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists only the current type, shows the match total, and imports the selection", async () => {
    const user = userEvent.setup();
    const listSpy = vi
      .spyOn(API, "listAssets")
      .mockResolvedValue({ items: ITEMS, total: 75, counts: { character: 75, scene: 4, prop: 0 } });
    const onImport = vi.fn();
    render(<AssetPickerModal type="character" existingNames={new Set(["李小红"])} onClose={() => {}} onImport={onImport} />);

    expect(await screen.findByText("匹配 75 个角色")).toBeInTheDocument();
    expect(listSpy).toHaveBeenCalledWith(expect.objectContaining({ type: "character", offset: 0 }), expect.anything());
    expect(screen.getByRole("button", { name: /李小红/ })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /王小明/ }));
    await user.click(screen.getByRole("button", { name: /小师妹/ }));
    await user.click(screen.getByRole("button", { name: "导入 2 个" }));
    expect(onImport).toHaveBeenCalledWith(["1", "2"]);
  });

  it("searches within the current type", async () => {
    const user = userEvent.setup();
    const listSpy = vi
      .spyOn(API, "listAssets")
      .mockResolvedValue({ items: [], total: 0, counts: { character: 0, scene: 0, prop: 0 } });
    render(<AssetPickerModal type="scene" existingNames={new Set()} onClose={() => {}} onImport={vi.fn()} />);

    await user.type(await screen.findByRole("searchbox", { name: "按名称搜索资产" }), "庙");

    await waitFor(() =>
      expect(listSpy).toHaveBeenLastCalledWith(expect.objectContaining({ type: "scene", q: "庙" }), expect.anything()),
    );
    expect(await screen.findByText("没有名称包含「庙」的场景")).toBeInTheDocument();
  });
});
