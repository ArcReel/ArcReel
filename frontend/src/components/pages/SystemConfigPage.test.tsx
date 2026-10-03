import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { SystemConfigPage } from "@/components/pages/SystemConfigPage";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
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
      text_backend_simple: "",
      text_backend_complex: "",
      video_generate_audio: true,
      video_poll_timeout_seconds: 3600,
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
        credential_count: 1,
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

function renderPage(path = "/app/settings", searchPath?: string) {
  const location = memoryLocation({ path, searchPath, record: true });
  render(
    <Router hook={location.hook}>
      <LeaveGuardProvider>
        <SystemConfigPage />
      </LeaveGuardProvider>
    </Router>,
  );
  return location;
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
      supports_base_url: false,
    } as never);
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
  });

  it("按生成、Agent、市场与使用记录、系统分组列出分区，无法识别的分区落在供应商", () => {
    renderPage("/app/settings", "section=media");
    const nav = screen.getByRole("navigation", { name: "设置" });

    const groups = within(nav)
      .getAllByRole("list")
      .map((list) => [
        list.getAttribute("aria-labelledby")
          ? document.getElementById(list.getAttribute("aria-labelledby")!)?.textContent
          : null,
        within(list).getAllByRole("link").map((link) => link.textContent),
      ]);
    expect(groups).toEqual([
      ["生成", ["供应商", "默认模型", "调用端点"]],
      ["Agent", ["ArcReel Agent", "Agent 记忆", "外部 Agent 接入", "访问令牌"]],
      [null, ["市场", "使用记录"]],
      ["系统", ["通用", "提示词模版", "关于"]],
    ]);
    expect(within(nav).getByRole("link", { name: "供应商" })).toHaveAttribute("aria-current", "page");
  });

  it("从侧栏切换分区时把分区写进地址，不保留上一个分区的定位参数", async () => {
    const user = userEvent.setup();
    const { history } = renderPage("/app/settings", "section=usage&u_project=demo");

    await user.click(screen.getByRole("link", { name: "通用" }));

    expect(await screen.findByRole("heading", { name: "通用", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "通用" })).toHaveAttribute("aria-current", "page");
    expect(history.at(-1)).toBe("/app/settings?section=general");
  });

  it("切换设置分区前拦截未保存的修改，放弃修改后才切换", async () => {
    const user = userEvent.setup();
    renderPage("/app/settings", "section=default-models");
    const models = screen.getByRole("link", { name: "默认模型" });
    const usage = screen.getByRole("link", { name: "使用记录" });

    const timeout = await screen.findByRole("textbox", { name: "视频轮询超时（秒）" });
    await user.clear(timeout);
    await user.type(timeout, "7200");
    await user.click(usage);

    const dialog = await screen.findByRole("alertdialog", { name: "有未保存的修改" });
    expect(models).toHaveAttribute("aria-current", "page");

    await user.click(within(dialog).getByRole("button", { name: "放弃修改" }));
    await waitFor(() => expect(usage).toHaveAttribute("aria-current", "page"));
  });

  it("只缺内嵌 Agent 时，提示与侧栏标记只在「ArcReel Agent」分区，大厅的配置完整状态不受影响", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(
      makeConfigResponse({ anthropic_api_key: { is_set: false, masked: null } }),
    );
    vi.spyOn(API, "getProviders").mockResolvedValue(makeProviders({ status: "ready" }));

    renderPage("/app/settings", "section=arcreel-agent");

    expect(await screen.findByRole("note", { name: "ArcReel Agent 尚未配置" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "设置" });
    const flagged = within(nav)
      .getAllByRole("link")
      .filter((link) => within(link).queryByRole("img", { name: "配置不完整" }))
      .map((link) => link.textContent);
    expect(flagged).toEqual(["ArcReel Agent"]);
    expect(useConfigStatusStore.getState().isComplete).toBe(true);

    await user.click(within(nav).getByRole("link", { name: "供应商" }));
    await waitFor(() => expect(within(nav).getByRole("link", { name: "供应商" })).toHaveAttribute("aria-current", "page"));
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("缺少供应商时只在「供应商」分区与其侧栏项提示，其他分区不出现提示", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getProviders").mockResolvedValue(makeProviders({ status: "unconfigured" }));
    vi.spyOn(API, "listPromptTemplates").mockResolvedValue({ templates: [] });

    renderPage("/app/settings", "section=providers");
    const notice = await screen.findByRole("note", { name: "配置不完整" });
    expect(notice).toHaveTextContent("未配置支持视频生成的供应商");

    const nav = screen.getByRole("navigation", { name: "设置" });
    const flagged = within(nav)
      .getAllByRole("link")
      .filter((link) => within(link).queryByRole("img", { name: "配置不完整" }))
      .map((link) => link.textContent);
    expect(flagged).toEqual(["供应商"]);

    for (const section of ["默认模型", "使用记录", "提示词模版"]) {
      await user.click(within(nav).getByRole("link", { name: section }));
      await waitFor(() => expect(within(nav).getByRole("link", { name: section })).toHaveAttribute("aria-current", "page"));
      expect(screen.queryByRole("note", { name: "配置不完整" })).not.toBeInTheDocument();
    }
  });

  it("Agent 记忆分区按用户级路径加载记忆文件", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue({
      path: "/data/users/default/memory",
      index: { exists: false, line_count: 0, byte_size: 0, over_limit: false },
      files: [],
    });

    renderPage("/app/settings", "section=agent-memory");

    expect(await screen.findByText("/data/users/default/memory")).toBeInTheDocument();
    expect(API.getAgentMemory).toHaveBeenCalledWith({ level: "user" }, expect.anything());
  });

  it("loads version info when entering the about section", async () => {
    renderPage("/app/settings", "section=about");

    expect(await screen.findByText("0.9.0")).toBeInTheDocument();
    expect(await screen.findByText(/最新版本：0.9.1/)).toBeInTheDocument();
    expect(await screen.findByText("发现新版本")).toBeInTheDocument();
  });

  it("rechecks updates when clicking the refresh button", async () => {
    const getSystemVersion = vi.spyOn(API, "getSystemVersion").mockResolvedValue(
      makeVersionResponse({ latest: null, has_update: false, update_check_error: "boom" }),
    );

    renderPage("/app/settings", "section=about");

    const button = await screen.findByRole("button", { name: /检查更新/ });
    fireEvent.click(button);

    await waitFor(() => {
      expect(getSystemVersion).toHaveBeenCalledTimes(2);
    });
  });
});
