import { describe, it, expect, beforeEach, vi } from "vitest";
import { useAssetsStore } from "./assets-store";
import { API } from "@/api";

describe("useAssetsStore", () => {
  beforeEach(() => {
    useAssetsStore.setState({ byType: { character: [], scene: [], prop: [] } });
    vi.restoreAllMocks();
  });

  it("loads list by type", async () => {
    vi.spyOn(API, "listAssets" as any).mockResolvedValue({ items: [{ id: "1", type: "scene", name: "A", description: "", voice_style: "", image_path: null, source_project: null, updated_at: null }] });
    await useAssetsStore.getState().loadList("scene");
    expect(useAssetsStore.getState().byType.scene).toHaveLength(1);
  });

  it("removes asset locally after delete", async () => {
    useAssetsStore.setState({ byType: { character: [], scene: [{ id: "1", type: "scene", name: "A", description: "", voice_style: "", image_path: null, source_project: null, updated_at: null }], prop: [] } });
    vi.spyOn(API, "deleteAsset" as any).mockResolvedValue(undefined);
    await useAssetsStore.getState().deleteAsset("1", "scene");
    expect(useAssetsStore.getState().byType.scene).toHaveLength(0);
  });
});
