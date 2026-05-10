import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { SystemConfigPage } from "@/components/pages/SystemConfigPage";
import { BRAND } from "@/branding";
import type { GetSystemConfigResponse, GetSystemVersionResponse, ProviderInfo } from "@/types";

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
      default_text_backend: "",
      text_backend_script: "",
      text_backend_overview: "",
      text_backend_style: "",
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
      text_backends: [],
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
        media_types: ["image", "video", "text"],
        capabilities: [],
        configured_keys: ["api_key"],
        missing_keys: [],
        models: {},
        ...overrides,
      },
    ],
  };
}

function makeVersionResponse(overrides?: Partial<GetSystemVersionResponse>): GetSystemVersionResponse {
  return {
    current: { version: "0.9.0" },
    latest: {
      version: "0.9.1",
      tag_name: "v0.9.1",
      name: "0.9.1",
      body: "## What's Changed\n- add about tab",
      html_url: "https://github.com/example/ArcReel/releases/tag/v0.9.1",
      published_at: "2026-04-21T08:00:00Z",
    },
    has_update: true,
    checked_at: "2026-04-21T09:00:00Z",
    update_check_error: null,
    ...overrides,
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
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "getSystemVersion").mockResolvedValue(makeVersionResponse());
    vi.spyOn(API, "getProviderConfig").mockResolvedValue({
      id: "gemini",
      display_name: "Google Gemini",
      status: "ready",
      media_types: ["image", "video"],
      capabilities: [],
      fields: [],
    } as never);
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
    vi.spyOn(API, "getUsageStatsGrouped").mockResolvedValue({ stats: [], period: { start: "", end: "" } });
  });

  it("renders the page header", () => {
    renderPage();
    expect(screen.getByText("设置")).toBeInTheDocument();
    expect(screen.getByText("系统配置与 API 访问管理")).toBeInTheDocument();
  });

  it("renders all 6 sidebar sections", () => {
    renderPage();
    expect(screen.getByRole("button", { name: /智能体/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /供应商/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /模型选择/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /用量统计/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /API 令牌/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /关于/ })).toBeInTheDocument();
  });

  it("defaults to the 供应商 section", () => {
    renderPage();
    const providersButton = screen.getByRole("button", { name: /供应商/ });
    // Active sidebar item carries aria-current="page" (Darkroom redesign)
    expect(providersButton).toHaveAttribute("aria-current", "page");
  });

  it("clicking 供应商 makes it the active section", async () => {
    renderPage();
    const providersButton = screen.getByRole("button", { name: /供应商/ });
    fireEvent.click(providersButton);
    await waitFor(() => {
      expect(providersButton).toHaveAttribute("aria-current", "page");
    });
  });

  it("clicking 模型选择 makes it the active section", async () => {
    renderPage();
    const mediaButton = screen.getByRole("button", { name: /模型选择/ });
    fireEvent.click(mediaButton);
    await waitFor(() => {
      expect(mediaButton).toHaveAttribute("aria-current", "page");
    });
  });

  it("clicking 用量统计 makes it the active section", async () => {
    renderPage();
    const usageButton = screen.getByRole("button", { name: /用量统计/ });
    fireEvent.click(usageButton);
    await waitFor(() => {
      expect(usageButton).toHaveAttribute("aria-current", "page");
    });
  });

  it("shows config warning banner when there are config issues", async () => {
    // Simulate unconfigured anthropic key to trigger an issue
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(
      makeConfigResponse({ anthropic_api_key: { is_set: false, masked: null } }),
    );
    vi.spyOn(API, "getProviders").mockResolvedValue(makeProviders({ status: "ready" }));

    // Banner renders inside non-providers content panes (providers section has its own UI),
    // so land on agent to assert it.
    renderPage("/app/settings?section=agent");

    await waitFor(() => {
      expect(screen.getByText("当前配置存在以下问题，可能会影响部分功能：")).toBeInTheDocument();
    });
    expect(
      screen.getByText(`${BRAND.name} 智能体 API Key`, { exact: false }),
    ).toBeInTheDocument();
  });

  it("does not show warning banner when config is complete", async () => {
    renderPage();

    // Give time for config status to load
    await waitFor(() => {
      expect(API.getProviders).toHaveBeenCalled();
    });

    expect(screen.queryByText("当前配置存在以下问题，可能会影响部分功能：")).not.toBeInTheDocument();
  });

  it("renders the back link that navigates to projects", () => {
    renderPage();
    const link = screen.getByRole("link", { name: "返回" });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/app/projects");
  });

  it("loads version info when entering the about section", async () => {
    renderPage("/app/settings?section=about");

    expect(await screen.findByText("0.9.0")).toBeInTheDocument();
    expect(await screen.findByText(/最新版本：0.9.1/)).toBeInTheDocument();
    expect(await screen.findByText("发现新版本")).toBeInTheDocument();
    expect(await screen.findByText("Release Notes")).toBeInTheDocument();
    expect(await screen.findByText(/add about tab/)).toBeInTheDocument();
  });

  it("rechecks updates when clicking the refresh button", async () => {
    const getSystemVersion = vi.spyOn(API, "getSystemVersion").mockResolvedValue(
      makeVersionResponse({ latest: null, has_update: false, update_check_error: "boom" }),
    );

    renderPage("/app/settings?section=about");

    const button = await screen.findByRole("button", { name: /检查更新/ });
    fireEvent.click(button);

    await waitFor(() => {
      expect(getSystemVersion).toHaveBeenCalledTimes(2);
    });
  });
});
