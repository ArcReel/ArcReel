import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { GetSystemConfigResponse } from "@/types";
import { PromptSettingsSection } from "./PromptSettingsSection";

const CONFIG: GetSystemConfigResponse = {
  settings: {
    default_video_backend: "",
    default_image_backend: "",
    default_text_backend: "",
    text_backend_simple: "",
    text_backend_complex: "",
    default_text_reasoning_effort: "",
    text_reasoning_effort_simple: "",
    text_reasoning_effort_complex: "",
    asset_prompt_character: "",
    asset_prompt_scene: "自定义场景规则",
    asset_prompt_prop: "",
    video_generate_audio: true,
    anthropic_api_key: { is_set: false, masked: null },
    anthropic_base_url: "",
    anthropic_model: "",
    anthropic_default_haiku_model: "",
    anthropic_default_opus_model: "",
    anthropic_default_sonnet_model: "",
    claude_code_subagent_model: "",
    agent_session_cleanup_delay_seconds: 300,
    agent_max_concurrent_sessions: 5,
  },
  options: {
    video_backends: [],
    image_backends: [],
    text_backends: [],
    asset_prompt_defaults: {
      character: "默认角色规则",
      scene: "默认场景规则",
      prop: "默认道具规则",
    },
  },
};

describe("PromptSettingsSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(CONFIG);
    vi.spyOn(API, "updateSystemConfig").mockResolvedValue(CONFIG);
  });

  it("shows stored prompts and built-in fallbacks", async () => {
    render(<PromptSettingsSection />);

    expect(await screen.findByLabelText("角色集提示词")).toHaveValue("默认角色规则");
    expect(screen.getByLabelText("场景库提示词")).toHaveValue("自定义场景规则");
    expect(screen.getByLabelText("道具库提示词")).toHaveValue("默认道具规则");
  });

  it("saves only the edited prompt field", async () => {
    render(<PromptSettingsSection />);
    const characterPrompt = await screen.findByLabelText("角色集提示词");

    fireEvent.change(characterPrompt, { target: { value: "新的角色规则" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(API.updateSystemConfig).toHaveBeenCalledWith({
        asset_prompt_character: "新的角色规则",
      });
    });
  });

  it("restores a prompt to the built-in default", async () => {
    render(<PromptSettingsSection />);
    await screen.findByLabelText("角色集提示词");

    fireEvent.click(screen.getAllByRole("button", { name: "恢复内置默认" })[1]);
    expect(screen.getByLabelText("场景库提示词")).toHaveValue("默认场景规则");

    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => {
      expect(API.updateSystemConfig).toHaveBeenCalledWith({ asset_prompt_scene: "" });
    });
  });
});
