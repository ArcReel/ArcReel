import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import "@/i18n";
import { API } from "@/api";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
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
      text_backend_simple: "",
      text_backend_complex: "",
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
  vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
}

function renderSection() {
  const location = memoryLocation({ path: "/app/settings", searchPath: "section=arcreel-agent", record: true });
  render(
    <Router hook={location.hook}>
      <LeaveGuardProvider>
        <AgentConfigTab />
      </LeaveGuardProvider>
    </Router>,
  );
  return location;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("AgentConfigTab", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("没有生效的 Agent 供应商时，在本分区就地提示内嵌 Agent 未配置", async () => {
    setupBaseMocks();
    useConfigStatusStore.setState({ initialized: true, isEmbeddedAgentConfigured: false });
    renderSection();

    expect(await screen.findByRole("alert")).toHaveTextContent("ArcReel Agent 尚未配置");
    expect(screen.getByText(/还没有 Agent 供应商/)).toBeInTheDocument();
  });

  it("每个 Agent 供应商列出默认模型与单独设置过的模型路由", async () => {
    setupBaseMocks({
      credentials: [makeCredential({ opus_model: "claude-opus-4", subagent_model: "claude-haiku-4-5" })],
    });
    useConfigStatusStore.setState({ initialized: true, isEmbeddedAgentConfigured: true });
    renderSection();

    const item = await screen.findByRole("listitem", { name: "Anthropic 主号" });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    const terms = within(item).getAllByRole("term").map((el) => el.textContent);
    const values = within(item).getAllByRole("definition").map((el) => el.textContent);
    expect(Object.fromEntries(terms.map((term, i) => [term, values[i]]))).toEqual({
      默认模型: "claude-sonnet-4",
      密钥: "sk-ant-***",
      "Opus 模型": "claude-opus-4",
      子智能体模型: "claude-haiku-4-5",
    });
  });

  it("删除 Agent 供应商先经确认，确认后从列表移除", async () => {
    const backup = makeCredential({ id: 2, display_name: "备用网关", is_active: false });
    setupBaseMocks({ credentials: [makeCredential(), backup] });
    vi.spyOn(API, "deleteAgentCredential").mockImplementation(async () => {
      vi.mocked(API.listAgentCredentials).mockResolvedValue({ credentials: [makeCredential()] });
    });
    renderSection();
    const user = userEvent.setup();

    const item = await screen.findByRole("listitem", { name: "备用网关" });
    await user.click(within(item).getByRole("button", { name: "删除" }));
    const dialog = await screen.findByRole("alertdialog", { name: "删除 Agent 供应商" });
    expect(dialog).toHaveTextContent("备用网关");
    await user.click(within(dialog).getByRole("button", { name: "删除供应商" }));

    expect(API.deleteAgentCredential).toHaveBeenCalledWith(2);
    await waitFor(() => expect(screen.queryByRole("listitem", { name: "备用网关" })).not.toBeInTheDocument());
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("生效中的 Agent 供应商不能删除", async () => {
    setupBaseMocks({ credentials: [makeCredential()] });
    renderSection();

    const item = await screen.findByRole("listitem", { name: "Anthropic 主号" });
    expect(within(item).getByRole("button", { name: "删除" })).toBeDisabled();
  });

  it("外部 Agent 与语言规范以链接指向对应分区", async () => {
    setupBaseMocks();
    renderSection();

    expect(await screen.findByRole("link", { name: "外部 Agent 接入" })).toHaveAttribute(
      "href",
      "/app/settings?section=external-agent",
    );
    expect(screen.getByRole("link", { name: "提示词模版" })).toHaveAttribute(
      "href",
      "/app/settings?section=prompt-templates&template=text%2Fagent_language_rule",
    );
  });

  it("运行参数默认折叠，修改后离开分区会被拦截", async () => {
    setupBaseMocks();
    const { history } = renderSection();
    const user = userEvent.setup();

    expect(screen.queryByRole("spinbutton", { name: "最大并发会话数" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "高级" }));
    const field = await screen.findByRole("spinbutton", { name: "最大并发会话数" });
    await waitFor(() => expect(field).toBeEnabled());
    await user.clear(field);
    await user.type(field, "8");

    await user.click(screen.getByRole("link", { name: "外部 Agent 接入" }));
    expect(await screen.findByRole("alertdialog", { name: "有未保存的修改" })).toBeInTheDocument();
    expect(history.at(-1)).toBe("/app/settings?section=arcreel-agent");
  });
});
