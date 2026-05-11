import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { AgentConfigTab } from "@/components/pages/AgentConfigTab";
import type { GetSystemConfigResponse } from "@/types";
import type {
  AgentCredential,
  PresetProvider,
} from "@/types/agent-credential";

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

function makePreset(overrides?: Partial<PresetProvider>): PresetProvider {
  return {
    id: "anthropic",
    display_name: "Anthropic",
    icon_key: "anthropic",
    messages_url: "https://api.anthropic.com",
    discovery_url: "https://api.anthropic.com/v1/models",
    default_model: "claude-sonnet-4",
    suggested_models: ["claude-sonnet-4", "claude-haiku-4-5"],
    docs_url: null,
    api_key_url: null,
    notes: null,
    api_key_pattern: null,
    is_recommended: true,
    ...overrides,
  };
}

function makeCredential(overrides?: Partial<AgentCredential>): AgentCredential {
  return {
    id: 1,
    preset_id: "anthropic",
    display_name: "Anthropic 主号",
    icon_key: "anthropic",
    base_url: "https://api.anthropic.com",
    api_key_masked: "sk-ant-***",
    model: "claude-sonnet-4",
    haiku_model: null,
    sonnet_model: null,
    opus_model: null,
    subagent_model: null,
    is_active: true,
    created_at: "2026-04-21T00:00:00Z",
    ...overrides,
  };
}

function setupBaseMocks(opts?: { credentials?: AgentCredential[] }) {
  vi.spyOn(API, "getSystemConfig").mockResolvedValue(makeConfigResponse());
  vi.spyOn(API, "listAgentCredentials").mockResolvedValue({
    credentials: opts?.credentials ?? [],
  });
  vi.spyOn(API, "listAgentPresetProviders").mockResolvedValue({
    providers: [makePreset()],
    custom_sentinel_id: "__custom__",
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("AgentConfigTab — credentials directory", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("renders empty hint when no credentials are present", async () => {
    setupBaseMocks();
    render(<AgentConfigTab visible />);

    expect(
      await screen.findByTestId("credential-list-empty"),
    ).toBeInTheDocument();
  });

  it('shows the "+ Add credential" button in Section 1', async () => {
    setupBaseMocks();
    render(<AgentConfigTab visible />);

    // Use translated text + leading "+"
    const btn = await screen.findByRole("button", { name: /\+ 添加密钥/ });
    expect(btn).toBeInTheDocument();
  });

  it("renders existing credentials in the list", async () => {
    setupBaseMocks({ credentials: [makeCredential()] });
    render(<AgentConfigTab visible />);

    expect(await screen.findByText("Anthropic 主号")).toBeInTheDocument();
    expect(
      screen.getByText(/sk-ant-\*\*\*/),
    ).toBeInTheDocument();
  });
});

describe("AgentConfigTab — discover models", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.restoreAllMocks();

    setupBaseMocks();
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

  it("calls discoverAnthropicModels with empty body (relies on backend active credential fallback)", async () => {
    render(<AgentConfigTab visible />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /获取模型|Discover Models/i }));

    await waitFor(() => {
      expect(API.discoverAnthropicModels).toHaveBeenCalledWith(
        {},
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });
  });

  it("shows error toast when discovery fails", async () => {
    vi.mocked(API.discoverAnthropicModels).mockRejectedValueOnce(new Error("boom"));

    render(<AgentConfigTab visible />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /获取模型|Discover Models/i }));

    await waitFor(() => {
      const toast = useAppStore.getState().toast;
      expect(toast?.text).toMatch(/boom/);
      expect(toast?.tone).toBe("error");
    });
  });

  it("shows success toast with model count on discovery", async () => {
    render(<AgentConfigTab visible />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /获取模型|Discover Models/i }));

    await waitFor(() => {
      const toast = useAppStore.getState().toast;
      expect(toast?.tone).toBe("success");
      expect(toast?.text).toMatch(/2/);
    });
  });
});
