import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { Asset } from "@/types/asset";
import { ProjectAssetLinkControl } from "./ProjectAssetLinkControl";

const asset: Asset = {
  id: "asset-1",
  type: "character",
  name: "鳄鱼爸爸",
  description: "全局角色",
  voice_style: "",
  image_path: null,
  audio_path: null,
  source_project: null,
  updated_at: null,
};

describe("ProjectAssetLinkControl", () => {
  it("shows a matched asset card and links it only after the user clicks", async () => {
    vi.spyOn(API, "getAsset").mockResolvedValue({ asset });
    const link = vi.spyOn(API, "linkProjectAsset").mockResolvedValue({
      success: true,
      project_asset: {},
      asset,
    });
    const user = userEvent.setup();
    render(
      <ProjectAssetLinkControl
        projectName="demo"
        resourceType="character"
        resourceId="鳄鱼爸爸"
        matchedAssetId="asset-1"
      />,
    );

    expect(await screen.findByTestId("global-asset-link-card")).toHaveTextContent("鳄鱼爸爸");
    expect(link).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "链接全局资产" }));

    await waitFor(() => expect(link).toHaveBeenCalledWith({
      project_name: "demo",
      resource_type: "character",
      resource_id: "鳄鱼爸爸",
      asset_id: "asset-1",
    }));
  });

  it("unlinks an already linked card", async () => {
    vi.spyOn(API, "getAsset").mockResolvedValue({ asset });
    const unlink = vi.spyOn(API, "unlinkProjectAsset").mockResolvedValue({
      success: true,
      project_asset: {},
    });
    const user = userEvent.setup();
    render(
      <ProjectAssetLinkControl
        projectName="demo"
        resourceType="character"
        resourceId="鳄鱼爸爸"
        linkedAssetId="asset-1"
      />,
    );

    await screen.findByTestId("global-asset-link-card");
    await user.click(screen.getByRole("button", { name: "解除全局资产链接" }));
    await waitFor(() => expect(unlink).toHaveBeenCalledWith("demo", "character", "鳄鱼爸爸"));
  });
});
