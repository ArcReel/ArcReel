import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { Asset } from "@/types/asset";
import {
  ProjectAssetImageUsageSwitch,
  ProjectAssetLinkControl,
  ProjectAssetVoiceSourceSwitch,
} from "./ProjectAssetLinkControl";

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
  it("shows an exact matched asset with an unlink icon", async () => {
    vi.spyOn(API, "getAsset").mockResolvedValue({ asset });
    const link = vi.spyOn(API, "linkProjectAsset").mockResolvedValue({
      success: true,
      project_asset: {},
      asset,
    });
    render(
      <ProjectAssetLinkControl
        projectName="demo"
        resourceType="character"
        resourceId="鳄鱼爸爸"
        matchedAssetId="asset-1"
      />,
    );

    await screen.findByTestId("global-asset-link-control");
    expect(link).not.toHaveBeenCalled();
    const unlinkButton = screen.getByRole("button", { name: "解除全局资产链接" });
    expect(unlinkButton.querySelector(".lucide-unlink")).toBeInTheDocument();
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

    await screen.findByTestId("global-asset-link-control");
    await user.click(screen.getByRole("button", { name: "解除全局资产链接" }));
    await waitFor(() => expect(unlink).toHaveBeenCalledWith("demo", "character", "鳄鱼爸爸"));
  });

  it("shows a link icon before an asset is linked", () => {
    render(
      <ProjectAssetLinkControl
        projectName="demo"
        resourceType="character"
        resourceId="鳄鱼爸爸"
      />,
    );

    const linkButton = screen.getByRole("button", { name: "链接全局资产" });
    expect(linkButton.querySelector(".lucide-link")).toBeInTheDocument();
  });

  it("moves a linked image from the main slot to the reference slot", async () => {
    const configure = vi.spyOn(API, "configureProjectAssetLink").mockResolvedValue({
      success: true,
      project_asset: {},
      asset,
    });
    const onReload = vi.fn();
    const user = userEvent.setup();
    render(
      <ProjectAssetImageUsageSwitch
        projectName="demo"
        resourceType="character"
        resourceId="鳄鱼爸爸"
        imageUsage="main"
        onReload={onReload}
      />,
    );

    await user.click(screen.getByRole("button", { name: "切换参考图" }));
    await waitFor(() => expect(configure).toHaveBeenCalledWith({
      project_name: "demo",
      resource_type: "character",
      resource_id: "鳄鱼爸爸",
      image_usage: "reference",
    }));
    expect(onReload).toHaveBeenCalled();
  });

  it("switches from reference audio to Voice ID", async () => {
    const voiceAsset = {
      ...asset,
      audio_path: "characters/voices/croco.wav",
      voice_id: "voice-croco-dad",
    };
    const configure = vi.spyOn(API, "configureProjectAssetLink").mockResolvedValue({
      success: true,
      project_asset: {},
      asset: voiceAsset,
    });
    const user = userEvent.setup();
    render(
      <ProjectAssetVoiceSourceSwitch
        projectName="demo"
        resourceType="character"
        resourceId="鳄鱼爸爸"
        asset={voiceAsset}
        voiceSource="reference_audio"
      />,
    );

    await user.click(screen.getByRole("button", { name: "切换 Voice ID" }));
    await waitFor(() => expect(configure).toHaveBeenCalledWith({
      project_name: "demo",
      resource_type: "character",
      resource_id: "鳄鱼爸爸",
      voice_source: "voice_id",
    }));
  });

  it("does not offer a voice switch without an alternate source", () => {
    render(
      <ProjectAssetVoiceSourceSwitch
        projectName="demo"
        resourceType="character"
        resourceId="鳄鱼爸爸"
        asset={{ ...asset, audio_path: "characters/voices/croco.wav" }}
        voiceSource="reference_audio"
      />,
    );

    expect(screen.queryByRole("button", { name: /切换/ })).not.toBeInTheDocument();
  });
});
