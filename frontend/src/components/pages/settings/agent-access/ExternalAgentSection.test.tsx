import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { API } from "@/api";
import { SystemConfigPage } from "@/components/pages/SystemConfigPage";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import type { ApiKeyInfo } from "@/types";

import { ExternalAgentSection } from "./ExternalAgentSection";

function stubClipboard(writeText: () => Promise<void> = () => Promise.resolve()) {
  const fn = vi.fn(writeText);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: fn } });
  return fn;
}

describe("ExternalAgentSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState(useAppStore.getInitialState(), true);
  });

  afterEach(() => {
    Reflect.deleteProperty(navigator, "clipboard");
  });

  it("按步骤给出安装命令、MCP 端点与给 AI Agent 的提示词，复制的是原样内容", async () => {
    const user = userEvent.setup();
    const writeText = stubClipboard();
    render(<ExternalAgentSection />);

    const steps = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(steps.map((step) => within(step).getByRole("heading").textContent)).toEqual([
      "安装公开 skills",
      "配置远程 MCP",
      "准备访问令牌",
    ]);
    // setup skills store plaintext tokens; step three reminds users to exclude configuration files from version control.
    expect(within(steps[2]).getByText(/加入 \.gitignore/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "复制安装命令" }));
    expect(writeText).toHaveBeenLastCalledWith("npx skills add ArcReel/skills");
    await user.click(screen.getByRole("button", { name: "复制 MCP 端点" }));
    expect(writeText).toHaveBeenLastCalledWith(`${window.location.origin}/mcp`);
    await user.click(screen.getByRole("button", { name: "复制提示词" }));
    // Prompts include installation-guide addresses only, never tokens.
    expect(writeText).toHaveBeenLastCalledWith(
      `帮我接入 ArcReel。请阅读并执行 ${window.location.origin}/agent-installation-guide.md`,
    );
  });

  it("剪贴板不可用时提示手动复制", async () => {
    const user = userEvent.setup();
    stubClipboard(() => Promise.reject(new Error("denied")));
    render(<ExternalAgentSection />);

    await user.click(screen.getByRole("button", { name: "复制安装命令" }));

    await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("复制失败，请手动选择并复制内容。"));
    expect(screen.getByRole("button", { name: "复制安装命令" })).toBeInTheDocument();
  });

  it("在第三步创建的令牌出现在「访问令牌」分区的列表里", async () => {
    // Stateful API fakes persist creations and read lists, simulating one backend.
    const issued: ApiKeyInfo[] = [];
    vi.spyOn(API, "listApiKeys").mockImplementation(() => Promise.resolve([...issued]));
    vi.spyOn(API, "createApiKey").mockImplementation((name) => {
      const token = {
        id: 7,
        name,
        key_prefix: "arc-ext",
        created_at: "2026-02-01T00:00:00Z",
        expires_at: null,
        last_used_at: null,
      };
      issued.push(token);
      return Promise.resolve({ ...token, key: "arc-ext-full-secret" });
    });
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    // Settings mount fetches configuration status; return empty results here.
    vi.spyOn(API, "getProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "getSystemConfig").mockRejectedValue(new Error("not needed"));

    const user = userEvent.setup();
    const location = memoryLocation({ path: "/app/settings", searchPath: "section=external-agent", record: true });
    render(
      <Router hook={location.hook}>
        <LeaveGuardProvider>
          <SystemConfigPage />
        </LeaveGuardProvider>
      </Router>,
    );

    await user.click(screen.getByRole("button", { name: "创建访问令牌" }));
    const dialog = await screen.findByRole("dialog", { name: "创建访问令牌" });
    await user.type(within(dialog).getByRole("textbox", { name: "名称" }), "我的外部 Agent");
    await user.click(within(dialog).getByRole("button", { name: "创建" }));
    await screen.findByRole("dialog", { name: "访问令牌已创建" });
    await user.click(screen.getByRole("button", { name: "完成" }));

    await user.click(screen.getByRole("link", { name: "管理访问令牌" }));

    expect(await screen.findByRole("heading", { name: "访问令牌", level: 2 })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "访问令牌列表" });
    expect(within(table).getByText("我的外部 Agent")).toBeInTheDocument();
    expect(within(table).getByText("arc-ext****")).toBeInTheDocument();
  });
});
