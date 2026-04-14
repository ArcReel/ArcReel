import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import "@/i18n";
import { API } from "@/api";
import { WizardStep2Models } from "./WizardStep2Models";

const mockSysConfig = {
  settings: {
    default_video_backend: "gemini-aistudio/veo-3",
    default_image_backend: "gemini-aistudio/nano-banana",
    default_text_backend: "",
    text_backend_script: "",
    text_backend_overview: "",
    text_backend_style: "",
    video_generate_audio: false,
    anthropic_api_key: { is_set: false, masked: null },
    anthropic_base_url: "",
    anthropic_model: "",
    anthropic_default_haiku_model: "",
    anthropic_default_opus_model: "",
    anthropic_default_sonnet_model: "",
    claude_code_subagent_model: "",
    agent_session_cleanup_delay_seconds: 0,
    agent_max_concurrent_sessions: 0,
  },
  options: {
    video_backends: ["gemini-aistudio/veo-3"],
    image_backends: ["gemini-aistudio/nano-banana"],
    text_backends: ["gemini-aistudio/g25"],
    provider_names: { "gemini-aistudio": "Gemini AI Studio" },
  },
};

const mockProviders = {
  providers: [
    {
      id: "gemini-aistudio",
      display_name: "Gemini AI Studio",
      description: "",
      status: "ready" as const,
      media_types: ["video", "image", "text"],
      capabilities: [],
      configured_keys: [],
      missing_keys: [],
      models: {
        "veo-3": {
          display_name: "veo-3",
          media_type: "video",
          capabilities: [],
          default: false,
          supported_durations: [4, 6, 8],
          duration_resolution_constraints: {},
        },
      },
    },
  ],
};

const baseValue = {
  videoBackend: "",
  imageBackend: "",
  textBackendScript: "",
  textBackendOverview: "",
  textBackendStyle: "",
  defaultDuration: null,
};

describe("WizardStep2Models", () => {
  beforeEach(() => {
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(mockSysConfig as never);
    vi.spyOn(API, "getProviders").mockResolvedValue(mockProviders);
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
  });

  it("shows loading state initially", () => {
    render(
      <WizardStep2Models
        value={baseValue}
        onChange={() => {}}
        onBack={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/loading|加载中/i)).toBeInTheDocument();
  });

  it("renders ModelConfigSection after data loads", async () => {
    render(
      <WizardStep2Models
        value={baseValue}
        onChange={() => {}}
        onBack={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    await waitFor(() =>
      expect(screen.queryByText(/loading|加载中/i)).not.toBeInTheDocument(),
    );
    // ModelConfigSection renders 5 selectors (1 video + 1 image + 3 text)
    expect(screen.getAllByRole("combobox")).toHaveLength(5);
  });

  it("calls onBack when previous button is clicked", async () => {
    const onBack = vi.fn();
    render(
      <WizardStep2Models
        value={baseValue}
        onChange={() => {}}
        onBack={onBack}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    await waitFor(() =>
      expect(screen.queryByText(/loading|加载中/i)).not.toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /上一步|Back/i }));
    expect(onBack).toHaveBeenCalledOnce();
  });

  it("calls onNext when next button is clicked", async () => {
    const onNext = vi.fn();
    render(
      <WizardStep2Models
        value={baseValue}
        onChange={() => {}}
        onBack={() => {}}
        onNext={onNext}
        onCancel={() => {}}
      />,
    );
    await waitFor(() =>
      expect(screen.queryByText(/loading|加载中/i)).not.toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /下一步|Next/i }));
    expect(onNext).toHaveBeenCalledOnce();
  });

  it("calls onCancel when cancel button is clicked", async () => {
    const onCancel = vi.fn();
    render(
      <WizardStep2Models
        value={baseValue}
        onChange={() => {}}
        onBack={() => {}}
        onNext={() => {}}
        onCancel={onCancel}
      />,
    );
    await waitFor(() =>
      expect(screen.queryByText(/loading|加载中/i)).not.toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /取消|Cancel/i }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("shows error state when API fails", async () => {
    vi.spyOn(API, "getSystemConfig").mockRejectedValueOnce(
      new Error("network down"),
    );
    render(
      <WizardStep2Models
        value={baseValue}
        onChange={() => {}}
        onBack={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(/network down/)).toBeInTheDocument(),
    );
  });
});
