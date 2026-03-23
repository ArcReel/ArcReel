import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { SystemConfigPage } from "@/components/pages/SystemConfigPage";
import type { GetSystemConfigResponse, ProviderInfo } from "@/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeConfigResponse(
  overrides?: Partial<GetSystemConfigResponse["settings"]>,
): GetSystemConfigResponse {
  return {
    settings: {
      default_video_backend: "gemini/veo-3",
      default_image_backend: "gemini/imagen-4",
      video_generate_audio: true,
      anthropic_api_key: { is_set: true, masked: "sk-ant-***" },
      anthropic_base_url: "",
      anthropic_model: "",
      anthropic_default_haiku_model: "",
      anthropic_default_opus_model: "",
      anthropic_default_sonnet_model: "",
      claude_code_subagent_model: "",
      agent_session_cleanup_delay_seconds: 300,
      agent_max_concurrent_sessions: 5,
      ...overrides,
    },
    options: {
      video_backends: ["gemini/veo-3"],
      image_backends: ["gemini/imagen-4"],
    },
  };
}

function makeProviders(overrides?: Partial<ProviderInfo>): { providers: ProviderInfo[] } {
  return {
    providers: [
      {
        id: "gemini",
        display_name: "Google Gemini",
        description: "Google Gemini API",
        status: "ready",
        media_types: ["image", "video"],
        capabilities: [],
        configured_keys: ["api_key"],
        missing_keys: [],
        ...overrides,
      },
    ],
  };
}

function renderPage(path = "/app/settings") {
  const location = memoryLocation({ path, record: true });
  return render(
    <Router hook={location.hook}>
      <SystemConfigPage />
    </Router>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("SystemConfigPage", () => {
  beforeEach(() => {
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.restoreAllMocks();

    // Default: silence child section network calls so tests don't hang
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(makeConfigResponse());
    vi.spyOn(API, "getProviders").mockResolvedValue(makeProviders());
    vi.spyOn(API, "getProviderConfig").mockResolvedValue({
      id: "gemini",
      display_name: "Google Gemini",
      status: "ready",
      media_types: ["image", "video"],
      capabilities: [],
      fields: [],
    } as never);
    vi.spyOn(API, "getUsageStatsGrouped").mockResolvedValue({ stats: [], period: { start: "", end: "" } });
  });

  it("renders the page header", () => {
    renderPage();
    expect(screen.getByText("设置")).toBeInTheDocument();
    expect(screen.getByText("系统配置与 API 访问管理")).toBeInTheDocument();
  });

  it("renders all 4 sidebar sections", () => {
    renderPage();
    expect(screen.getByRole("button", { name: /智能体/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /供应商/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /图片\/视频/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /用量统计/ })).toBeInTheDocument();
  });

  it("defaults to the 智能体 section", () => {
    renderPage();
    const agentButton = screen.getByRole("button", { name: /智能体/ });
    // Active sidebar item has the indigo border class applied
    expect(agentButton.className).toContain("border-indigo-500");
  });

  it("clicking 供应商 makes it the active section", async () => {
    renderPage();
    const providersButton = screen.getByRole("button", { name: /供应商/ });
    fireEvent.click(providersButton);
    await waitFor(() => {
      expect(providersButton.className).toContain("border-indigo-500");
    });
  });

  it("clicking 图片/视频 makes it the active section", async () => {
    renderPage();
    const mediaButton = screen.getByRole("button", { name: /图片\/视频/ });
    fireEvent.click(mediaButton);
    await waitFor(() => {
      expect(mediaButton.className).toContain("border-indigo-500");
    });
  });

  it("clicking 用量统计 makes it the active section", async () => {
    renderPage();
    const usageButton = screen.getByRole("button", { name: /用量统计/ });
    fireEvent.click(usageButton);
    await waitFor(() => {
      expect(usageButton.className).toContain("border-indigo-500");
    });
  });

  it("shows config warning banner when there are config issues", async () => {
    // Simulate unconfigured anthropic key to trigger an issue
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(
      makeConfigResponse({ anthropic_api_key: { is_set: false, masked: null } }),
    );
    vi.spyOn(API, "getProviders").mockResolvedValue(makeProviders({ status: "ready" }));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("以下必填配置尚未完成：")).toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: /ArcReel 智能体 API Key/ }),
    ).toBeInTheDocument();
  });

  it("does not show warning banner when config is complete", async () => {
    renderPage();

    // Give time for config status to load
    await waitFor(() => {
      expect(API.getProviders).toHaveBeenCalled();
    });

    expect(screen.queryByText("以下必填配置尚未完成：")).not.toBeInTheDocument();
  });

  it("renders the back button that links to projects", () => {
    renderPage();
    expect(screen.getByRole("button", { name: "返回项目大厅" })).toBeInTheDocument();
  });
});
