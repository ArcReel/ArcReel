import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { SocialPublishProfilesResponse, SocialPublishProgress } from "@/types/social-publish";
import { PublishToSocialDialog } from "./PublishToSocialDialog";

const profiles: SocialPublishProfilesResponse = {
  active_profile: "studio",
  video_platforms: ["tiktok", "youtube", "x"],
  profiles: [
    {
      username: "studio",
      accounts: [
        { platform: "tiktok", display_name: "Studio", handle: "studio", reauth_required: false },
        { platform: "youtube", display_name: "Studio", handle: "studio", reauth_required: true },
        // reddit 不收视频：不该出现在可勾选的平台里
        { platform: "reddit", display_name: "Studio", handle: "studio", reauth_required: false },
      ],
    },
    { username: "otro", accounts: [] },
  ],
};

function renderDialog() {
  return render(
    <PublishToSocialDialog
      open
      onClose={() => undefined}
      projectName="demo"
      resourceType="videos"
      resourceId="E1S01"
      variant="post_production"
      videoVersion={3}
      audioVersion={2}
    />,
  );
}

describe("PublishToSocialDialog", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(API, "getSocialPublishProfiles").mockResolvedValue(profiles);
  });

  it("offers only connected accounts that accept video and blocks the ones needing reauthorization", async () => {
    renderDialog();

    const tiktok = await screen.findByRole("checkbox", { name: /tiktok/ });
    expect(tiktok).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: /youtube/ })).toBeDisabled();
    expect(screen.queryByRole("checkbox", { name: /reddit/ })).not.toBeInTheDocument();
  });

  it("publishes the selected platforms pinned to the previewed version", async () => {
    const publish = vi.spyOn(API, "publishPresentation").mockResolvedValue({
      request_id: "arcreel-1",
      job_id: null,
      scheduled_date: null,
      total_platforms: 1,
    });
    vi.spyOn(API, "getSocialPublishStatus").mockResolvedValue({
      request_id: "arcreel-1",
      job_id: null,
      status: "completed",
      completed: 1,
      total: 1,
      terminal: true,
      outcomes: [],
    } satisfies SocialPublishProgress);
    const user = userEvent.setup();
    renderDialog();

    await user.click(await screen.findByRole("checkbox", { name: /tiktok/ }));
    await user.click(screen.getByRole("button", { name: "发布" }));

    await waitFor(() => expect(publish).toHaveBeenCalledTimes(1));
    expect(publish).toHaveBeenCalledWith(
      "demo",
      "videos",
      "E1S01",
      expect.objectContaining({
        platforms: ["tiktok"],
        title: "E1S01",
        variant: "post_production",
        video_version: 3,
        audio_version: 2,
      }),
    );
  });

  it("keeps publishing disabled until a platform is picked", async () => {
    renderDialog();

    await screen.findByRole("checkbox", { name: /tiktok/ });
    expect(screen.getByRole("button", { name: "发布" })).toBeDisabled();
  });

  it("shows the post link once a platform finishes", async () => {
    vi.spyOn(API, "publishPresentation").mockResolvedValue({
      request_id: "arcreel-1",
      job_id: null,
      scheduled_date: null,
      total_platforms: 1,
    });
    vi.spyOn(API, "getSocialPublishStatus").mockResolvedValue({
      request_id: "arcreel-1",
      job_id: null,
      status: "completed",
      completed: 1,
      total: 1,
      terminal: true,
      outcomes: [
        {
          platform: "tiktok",
          status: "completed",
          success: true,
          url: "https://tiktok.com/@studio/video/1",
          error: null,
        },
      ],
    });
    const user = userEvent.setup();
    renderDialog();

    await user.click(await screen.findByRole("checkbox", { name: /tiktok/ }));
    await user.click(screen.getByRole("button", { name: "发布" }));

    const link = await screen.findByRole("link", { name: /查看贴文/ });
    expect(link).toHaveAttribute("href", "https://tiktok.com/@studio/video/1");
  });

  it("surfaces a missing credential instead of an empty platform list", async () => {
    vi.spyOn(API, "getSocialPublishProfiles").mockRejectedValue(new Error("尚未配置社交分发"));
    renderDialog();

    expect(await screen.findByRole("alert")).toHaveTextContent("尚未配置社交分发");
  });
});
