import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { GetSystemConfigResponse } from "@/types/system";
import { SocialPublishSection } from "./SocialPublishSection";

function config(overrides: Record<string, unknown> = {}): GetSystemConfigResponse {
  return {
    settings: {
      default_video_backend: "",
      default_image_backend: "",
      default_text_backend: "",
      text_backend_simple: "",
      text_backend_complex: "",
      video_generate_audio: false,
      video_poll_timeout_seconds: 600,
      anthropic_api_key: { is_set: false, masked: null },
      anthropic_base_url: "",
      anthropic_model: "",
      anthropic_default_haiku_model: "",
      anthropic_default_opus_model: "",
      anthropic_default_sonnet_model: "",
      claude_code_subagent_model: "",
      agent_session_cleanup_delay_seconds: 300,
      agent_max_concurrent_sessions: 5,
      upload_post_api_key: { is_set: false },
      upload_post_profile: "",
      upload_post_base_url: "",
      ...overrides,
    },
    options: { video_backends: [], image_backends: [], text_backends: [] },
  } as GetSystemConfigResponse;
}

describe("SocialPublishSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("never renders the stored key, not even a masked fragment of it", async () => {
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(
      config({ upload_post_api_key: { is_set: true }, upload_post_profile: "studio" }),
    );

    render(<SocialPublishSection />);

    const input = await screen.findByLabelText("API Key");
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("placeholder", "已配置（不回显）");
    expect(screen.getByLabelText("档案名")).toHaveValue("studio");
  });

  it("reports a failed config load with a retry instead of spinning forever", async () => {
    const getConfig = vi
      .spyOn(API, "getSystemConfig")
      .mockRejectedValueOnce(new Error("no se pudo leer la configuración"))
      .mockResolvedValue(config({ upload_post_profile: "studio" }));
    const user = userEvent.setup();
    render(<SocialPublishSection />);

    expect(await screen.findByRole("alert")).toHaveTextContent("no se pudo leer la configuración");

    await user.click(screen.getByRole("button", { name: "重试" }));

    expect(await screen.findByLabelText("档案名")).toHaveValue("studio");
    expect(getConfig).toHaveBeenCalledTimes(2);
  });

  it("saves the credential and reloads the connected accounts with it", async () => {
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(config());
    const update = vi.spyOn(API, "updateSystemConfig").mockResolvedValue(config());
    const profiles = vi.spyOn(API, "getSocialPublishProfiles").mockResolvedValue({
      active_profile: "studio",
      video_platforms: ["tiktok"],
      profiles: [
        {
          username: "studio",
          accounts: [
            { platform: "tiktok", display_name: "Studio", handle: "@studio", reauth_required: false },
          ],
        },
      ],
    });
    const user = userEvent.setup();
    render(<SocialPublishSection />);

    await user.type(await screen.findByLabelText("档案名"), "studio");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(update).toHaveBeenCalledWith({ upload_post_profile: "studio" }));
    await waitFor(() => expect(profiles).toHaveBeenCalled());
  });

  it("reports why the accounts could not be listed instead of showing an empty list", async () => {
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(config());
    vi.spyOn(API, "getSocialPublishProfiles").mockRejectedValue(new Error("凭证被拒绝"));
    const user = userEvent.setup();
    render(<SocialPublishSection />);

    await user.click(await screen.findByRole("button", { name: "读取账号" }));

    expect(await screen.findByText("凭证被拒绝")).toBeInTheDocument();
  });
});
