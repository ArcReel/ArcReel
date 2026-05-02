import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { AgentConfigTab } from "@/components/pages/AgentConfigTab";
import type { GetSystemConfigResponse } from "@/types";
import type { CustomProviderInfo } from "@/types/custom-provider";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeConfigResponse(): GetSystemConfigResponse {
  return {
    settings: {
      default_video_backend: "",
      default_image_backend: "",
      default_text_backend: "",
      text_backend_script: "",
      text_backend_overview: "",
      text_backend_style: "",
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
    },
  } as unknown as GetSystemConfigResponse;
}

function makeProvider(overrides?: Partial<CustomProviderInfo>): CustomProviderInfo {
  return {
    id: 1,
    display_name: "OneAPI",
    discovery_format: "openai",
    base_url: "https://oneapi.example.com",
    api_key_masked: "sk-***",
    models: [],
    created_at: "2026-04-21T00:00:00Z",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("AgentConfigTab — provider import", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.restoreAllMocks();

    vi.spyOn(API, "getSystemConfig").mockResolvedValue(makeConfigResponse());
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [makeProvider()] });
    vi.spyOn(API, "getCustomProviderCredentials").mockResolvedValue({
      base_url: "https://oneapi.example.com",
      api_key: "sk-secret",
    });
  });

  it("imports credentials from a custom provider into the API credentials inputs", async () => {
    render(<AgentConfigTab visible />);

    // Wait for the import button to appear (after initial load)
    const importButton = await screen.findByRole("button", { name: /从供应商导入/ });
    fireEvent.click(importButton);

    // Click the OneAPI entry in the dropdown
    const providerOption = await screen.findByRole("button", { name: "OneAPI" });
    fireEvent.click(providerOption);

    // Assert the API Key + Base URL inputs received the credential values
    await waitFor(() => {
      const keyInput = screen.getByLabelText("Anthropic API 密钥") as HTMLInputElement;
      const baseUrlInput = screen.getByLabelText("API 代理地址") as HTMLInputElement;
      expect(keyInput.value).toBe("sk-secret");
      expect(baseUrlInput.value).toBe("https://oneapi.example.com");
    });

    expect(API.getCustomProviderCredentials).toHaveBeenCalledWith(1);
  });
});

describe("AgentConfigTab — discover models", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.restoreAllMocks();

    const cfg = makeConfigResponse();
    cfg.settings.anthropic_api_key = { is_set: true, masked: "sk-ant-***" };
    cfg.settings.anthropic_base_url = "https://example.com";
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(cfg);
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "discoverAnthropicModels").mockResolvedValue({
      models: [
        {
          model_id: "claude-haiku-4-5",
          display_name: "Haiku 4.5",
          endpoint: "",
          is_default: false,
          is_enabled: true,
        },
        {
          model_id: "claude-opus-4-7",
          display_name: "Opus 4.7",
          endpoint: "",
          is_default: false,
          is_enabled: true,
        },
      ],
    });
  });

  it("renders combobox options after clicking discover", async () => {
    render(<AgentConfigTab visible />);

    const user = userEvent.setup();
    const btn = await screen.findByRole("button", { name: /获取模型|Discover Models/i });
    await user.click(btn);

    // Wait for discover request to complete + populate candidates
    await waitFor(() => {
      expect(API.discoverAnthropicModels).toHaveBeenCalled();
    });

    // Open the default-model Combobox
    const modelInput = await screen.findByRole("combobox", { name: "默认模型" });
    await user.click(modelInput);

    const options = await screen.findAllByRole("option");
    const labels = options.map((o) => o.textContent);
    expect(labels).toEqual(
      expect.arrayContaining(["claude-haiku-4-5", "claude-opus-4-7"]),
    );
  });

  it("sends undefined api_key when draft is empty (lets backend fallback)", async () => {
    render(<AgentConfigTab visible />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /获取模型|Discover Models/i }));

    await waitFor(() => {
      expect(API.discoverAnthropicModels).toHaveBeenCalledWith(
        { base_url: "https://example.com", api_key: undefined },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });
  });

  it("shows error when discovery fails", async () => {
    vi.mocked(API.discoverAnthropicModels).mockRejectedValueOnce(new Error("boom"));

    render(<AgentConfigTab visible />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /获取模型|Discover Models/i }));

    await waitFor(() => {
      expect(screen.getByText(/boom/)).toBeInTheDocument();
    });
  });
});
