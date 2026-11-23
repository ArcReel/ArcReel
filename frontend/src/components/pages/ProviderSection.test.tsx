import { render, screen, waitFor, within, act, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import i18n from "@/i18n";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import { createDeferred } from "@/test/deferred";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { ProviderSection } from "./ProviderSection";
import type { ProviderConfigDetail, ProviderInfo, CustomProviderInfo, EndpointDescriptor } from "@/types";

function renderAt(path = "/app/settings?section=providers&provider=gemini-aistudio") {
  const location = memoryLocation({ path, record: true });
  return {
    ...render(
      <Router hook={location.hook} searchHook={location.searchHook}>
        <LeaveGuardProvider>
          <ProviderSection />
        </LeaveGuardProvider>
      </Router>,
    ),
    location,
  };
}

function providersFor(lang: string): { providers: ProviderInfo[] } {
  return {
    providers: [
      {
        id: "gemini-aistudio",
        display_name: lang === "en" ? "Gemini AI Studio (EN)" : "Gemini AI Studio（中文）",
        description: "",
        status: "ready",
        media_types: ["video"],
        capabilities: [],
        credential_count: 0,
        models: {},
      },
    ],
  };
}

function customFor(lang: string): { providers: CustomProviderInfo[] } {
  return {
    providers: [
      {
        id: 1,
        display_name: lang === "en" ? "My Endpoint (EN)" : "我的端点（中文）",
        discovery_format: "openai",
        base_url: "https://example.invalid",
        api_key_masked: "sk-***",
        models: [],
        created_at: "2026-01-01T00:00:00Z",
        image_max_workers: null,
        video_max_workers: null,
        audio_max_workers: null,
      },
    ],
  };
}

function providerDetailFor(lang: string): ProviderConfigDetail {
  return {
    id: "gemini-aistudio",
    display_name: lang === "en" ? "Gemini AI Studio (EN)" : "Gemini AI Studio（中文）",
    description: "",
    status: "ready",
    media_types: ["video"],
    fields: [
      {
        key: "max_workers",
        label: "Max Workers",
        type: "number",
        required: false,
        is_set: true,
        value: "2",
      },
    ],
    supports_base_url: false,
    secret_fields: [],
    secret_field_groups: [],
  };
}

async function savePresetProvider() {
  renderAt();
  await screen.findByRole("heading", { name: "Gemini AI Studio（中文）" });
  fireEvent.change(screen.getByRole("spinbutton", { name: "Max Workers" }), {
    target: { value: "7" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(API.patchProviderConfig).toHaveBeenCalledWith("gemini-aistudio", { max_workers: "7" }));
}

const CHAT_ENDPOINT: EndpointDescriptor = {
  key: "openai-chat",
  media_type: "text",
  family: "openai",
  kind: "python",
  source: "builtin",
  display_name_key: "endpoint_openai_chat",
  display_name: null,
  request_method: "POST",
  request_path_template: "/v1/chat/completions",
  image_capabilities: null,
  end_image_capable: false,
  size_fixed: false,
  duration_fixed: false,
  duration_frame_rate_missing: false,
  duration_tier_empty: false,
  native_resolution: null,
  requests: [],
};

function customProvider(id: number, displayName: string): CustomProviderInfo {
  return {
    id,
    display_name: displayName,
    discovery_format: "openai",
    base_url: "https://example.invalid",
    api_key_masked: "sk-***",
    models: [],
    created_at: "2026-01-01T00:00:00Z",
    image_max_workers: null,
    video_max_workers: null,
    audio_max_workers: null,
  };
}

function saveNewCustomProvider() {
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "我的中转站" } });
  fireEvent.change(screen.getByLabelText("接口地址"), { target: { value: "https://api.example.invalid" } });
  fireEvent.change(screen.getByLabelText("密钥"), { target: { value: "sk-live" } });
  fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
  fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "gpt-4o" } });
  fireEvent.click(screen.getByRole("button", { name: "保存" }));
}

describe("ProviderSection", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.spyOn(useConfigStatusStore.getState(), "refresh").mockResolvedValue();
    vi.spyOn(API, "getProviders").mockImplementation(() =>
      Promise.resolve(providersFor(i18n.language)),
    );
    vi.spyOn(API, "listCustomProviders").mockImplementation(() =>
      Promise.resolve(customFor(i18n.language)),
    );
    vi.spyOn(API, "getProviderConfig").mockRejectedValue(new Error("detail not under test"));
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
    vi.spyOn(API, "patchProviderConfig").mockResolvedValue();
    vi.spyOn(API, "getProviderMultiplier").mockResolvedValue({ multiplier: 1, default: 1 });
  });

  afterEach(async () => {
    await act(async () => {
      await i18n.changeLanguage("zh");
    });
    vi.restoreAllMocks();
  });

  it("refetches the provider catalog when the interface language changes", async () => {
    renderAt();

    const nav = () => screen.getByRole("navigation");
    await screen.findByRole("navigation");
    expect(within(nav()).getAllByText("Gemini AI Studio（中文）")[0]).toBeInTheDocument();
    expect(within(nav()).getAllByText("我的端点（中文）")[0]).toBeInTheDocument();

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    await waitFor(() =>
      expect(within(nav()).getAllByText("Gemini AI Studio (EN)")[0]).toBeInTheDocument(),
    );
    expect(within(nav()).getAllByText("My Endpoint (EN)")[0]).toBeInTheDocument();
    expect(within(nav()).queryAllByText("Gemini AI Studio（中文）")).toHaveLength(0);
  });

  it("keeps the catalog rendered while the language-triggered refetch is in flight", async () => {
    let releaseRefetch: (() => void) | null = null;
    vi.spyOn(API, "getProviders").mockImplementation(() => {
      const lang = i18n.language;
      if (lang !== "en") return Promise.resolve(providersFor(lang));
      return new Promise((resolve) => {
        releaseRefetch = () => resolve(providersFor(lang));
      });
    });

    renderAt();
    await screen.findByRole("navigation");

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    expect(screen.queryByText(/加载供应商列表|Loading providers/)).not.toBeInTheDocument();
    expect(
      within(screen.getByRole("navigation")).getAllByText("Gemini AI Studio（中文）")[0],
    ).toBeInTheDocument();

    await act(async () => {
      releaseRefetch?.();
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(
        within(screen.getByRole("navigation")).getAllByText("Gemini AI Studio (EN)")[0],
      ).toBeInTheDocument(),
    );
  });

  it("keeps the previous catalog when a language-triggered refetch fails", async () => {
    vi.spyOn(API, "getProviders").mockImplementation(() => {
      const lang = i18n.language;
      if (lang !== "en") return Promise.resolve(providersFor(lang));
      return Promise.reject(new Error("network down"));
    });

    renderAt();
    await screen.findByRole("navigation");

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    await waitFor(() =>
      expect(
        within(screen.getByRole("navigation")).getAllByText("Gemini AI Studio（中文）")[0],
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("network down")).not.toBeInTheDocument();
  });

  it("surfaces the error when the language switches before any catalog has loaded", async () => {
    vi.spyOn(API, "getProviders").mockImplementation(() => {
      if (i18n.language !== "en") return new Promise<never>(() => {});
      return Promise.reject(new Error("network down"));
    });

    renderAt();

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    await screen.findByText("network down");
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("selects the first preset provider when the URL names no selection", async () => {
    const { location } = renderAt("/app/settings");

    const panel = await screen.findByRole("tabpanel");
    await waitFor(() =>
      expect(within(panel).getByRole("link", { name: /Gemini AI Studio（中文）/ })).toHaveAttribute("aria-current", "page"),
    );
    expect(location.history).toEqual(["/app/settings?section=providers&provider=gemini-aistudio"]);
  });

  it("warns when a successful save is followed by a failed catalog refresh", async () => {
    vi.mocked(API.getProviders)
      .mockReset()
      .mockResolvedValueOnce(providersFor("zh"))
      .mockRejectedValueOnce(new Error("network down"));
    vi.mocked(API.getProviderConfig).mockImplementation(() => Promise.resolve(providerDetailFor(i18n.language)));

    await savePresetProvider();

    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({
        text: "已保存，但供应商列表刷新失败，请重新加载页面",
        tone: "warning",
      }),
    );
  });

  it("does not warn when a successful save refresh is superseded", async () => {
    const superseded = createDeferred<{ providers: ProviderInfo[] }>();
    vi.mocked(API.getProviders)
      .mockReset()
      .mockResolvedValueOnce(providersFor("zh"))
      .mockReturnValueOnce(superseded.promise)
      .mockImplementation(() => Promise.resolve(providersFor(i18n.language)));
    vi.mocked(API.getProviderConfig).mockImplementation(() => Promise.resolve(providerDetailFor(i18n.language)));

    await savePresetProvider();
    await waitFor(() => expect(API.getProviders).toHaveBeenCalledTimes(2));

    await act(async () => i18n.changeLanguage("en"));
    superseded.resolve(providersFor("zh"));
    await waitFor(() => expect(API.getProviders).toHaveBeenCalledTimes(3));
    await act(async () => Promise.resolve());

    expect(useAppStore.getState().toast).toBeNull();
  });
  it("切换供应商前拦截未保存的高级配置，保存并离开会先保存再切换", async () => {
    vi.mocked(API.getProviderConfig).mockImplementation(() => Promise.resolve(providerDetailFor(i18n.language)));
    vi.spyOn(API, "getCustomProvider").mockResolvedValue(customProvider(1, "我的端点（中文）"));
    const { location } = renderAt();
    await screen.findByRole("heading", { name: "Gemini AI Studio（中文）" });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max Workers" }), { target: { value: "7" } });

    fireEvent.click(screen.getByRole("tab", { name: /自定义/ }));
    fireEvent.click(within(screen.getByRole("tabpanel")).getByRole("link", { name: /我的端点（中文）/ }));
    const dialog = await screen.findByRole("alertdialog", { name: "有未保存的修改" });
    expect(location.history.at(-1)).toBe("/app/settings?section=providers&provider=gemini-aistudio");

    fireEvent.click(within(dialog).getByRole("button", { name: "保存并离开" }));
    await waitFor(() => expect(location.history.at(-1)).toBe("/app/settings?section=providers&custom=1"));
    expect(API.patchProviderConfig).toHaveBeenCalledWith("gemini-aistudio", { max_workers: "7" });
  });

  it("shows each preset provider's key count from the catalog, or unconfigured when it has none", async () => {
    vi.mocked(API.getProviders).mockResolvedValue({
      providers: [
        { ...providersFor("zh").providers[0], credential_count: 2 },
        { ...providersFor("zh").providers[0], id: "grok", display_name: "Grok", status: "unconfigured", credential_count: 0 },
      ],
    });
    renderAt();

    const panel = await screen.findByRole("tabpanel");
    expect(within(panel).getByRole("link", { name: /Gemini AI Studio（中文）/ })).toHaveTextContent("已配置 2 个密钥");
    expect(within(panel).getByRole("link", { name: /Grok/ })).toHaveTextContent("未配置");
  });

  it("selects the newly created custom provider after the form saves", async () => {
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: [CHAT_ENDPOINT] });
    vi.spyOn(API, "getCustomProvider").mockResolvedValue(customProvider(2, "我的中转站"));
    vi.spyOn(API, "createCustomProvider").mockResolvedValue(customProvider(2, "我的中转站"));
    vi.mocked(API.listCustomProviders)
      .mockReset()
      .mockResolvedValueOnce({ providers: [customProvider(1, "旧端点")] })
      .mockResolvedValue({ providers: [customProvider(1, "旧端点"), customProvider(2, "我的中转站")] });

    const { location } = renderAt("/app/settings?section=providers&custom=new");
    await screen.findByRole("button", { name: "保存" });

    saveNewCustomProvider();

    await waitFor(() => expect(location.history.at(-1)).toBe("/app/settings?section=providers&custom=2"));
    await waitFor(() => expect(API.getCustomProvider).toHaveBeenCalledWith(2, { signal: expect.any(AbortSignal) }));
  });

  it("still selects the created provider when the catalog refresh fails", async () => {
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: [CHAT_ENDPOINT] });
    vi.spyOn(API, "getCustomProvider").mockResolvedValue(customProvider(2, "我的中转站"));
    vi.spyOn(API, "createCustomProvider").mockResolvedValue(customProvider(2, "我的中转站"));
    vi.mocked(API.listCustomProviders)
      .mockReset()
      .mockResolvedValueOnce({ providers: [customProvider(1, "旧端点")] })
      .mockRejectedValue(new Error("network down"));

    const { location } = renderAt("/app/settings?section=providers&custom=new");
    await screen.findByRole("button", { name: "保存" });

    saveNewCustomProvider();

    await waitFor(() => expect(location.history.at(-1)).toBe("/app/settings?section=providers&custom=2"));
    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({
        text: "已保存，但供应商列表刷新失败，请重新加载页面",
        tone: "warning",
      }),
    );
  });

  it("opens a custom provider together with its provider multiplier", async () => {
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: [CHAT_ENDPOINT] });
    vi.spyOn(API, "getCustomProvider").mockResolvedValue(customProvider(1, "旧端点"));
    vi.mocked(API.getProviderMultiplier).mockResolvedValue({ multiplier: 0.8, default: 1 });

    renderAt("/app/settings?section=providers&custom=1");

    expect(await screen.findByRole("textbox", { name: "供应商倍率" })).toHaveValue("0.8");
    expect(API.getProviderMultiplier).toHaveBeenCalledWith("custom-1", { signal: expect.any(AbortSignal) });
  });

  it("deleting a custom provider with unsaved edits goes straight to the first preset", async () => {
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: [CHAT_ENDPOINT] });
    vi.spyOn(API, "getCustomProvider").mockResolvedValue(customProvider(1, "旧端点"));
    vi.spyOn(API, "deleteCustomProvider").mockResolvedValue();

    const { location } = renderAt("/app/settings?section=providers&custom=1");
    fireEvent.change(await screen.findByLabelText("名称"), { target: { value: "改过的名字" } });
    fireEvent.click(screen.getByRole("button", { name: "更多操作" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "删除供应商" }));
    const dialog = await screen.findByRole("alertdialog", { name: "删除供应商「旧端点」？" });
    fireEvent.click(within(dialog).getByRole("button", { name: "删除供应商" }));

    await waitFor(() =>
      expect(location.history.at(-1)).toBe("/app/settings?section=providers&provider=gemini-aistudio"),
    );
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  describe("multi-channel vendor grouping", () => {
    const groupedCatalog: ProviderInfo[] = [
      {
        id: "gemini-aistudio",
        display_name: "AI Studio",
        description: "",
        status: "ready",
        media_types: ["image"],
        capabilities: [],
        credential_count: 1,
        models: {},
      },
      {
        id: "gemini-vertex",
        display_name: "Vertex AI",
        description: "",
        status: "unconfigured",
        media_types: ["video"],
        capabilities: [],
        credential_count: 0,
        models: {},
      },
      {
        id: "ark",
        display_name: "火山方舟",
        description: "",
        status: "unconfigured",
        media_types: ["video"],
        capabilities: [],
        credential_count: 0,
        models: {},
      },
      {
        id: "ark-agent-plan",
        display_name: "火山方舟 Agent Plan",
        description: "",
        status: "ready",
        media_types: ["text", "image"],
        capabilities: [],
        credential_count: 2,
        models: {},
      },
      {
        id: "grok",
        display_name: "Grok",
        description: "",
        status: "unconfigured",
        media_types: ["image"],
        capabilities: [],
        credential_count: 0,
        models: {},
      },
    ];

    function detailByProviderId(id: string): ProviderConfigDetail {
      const item = groupedCatalog.find((p) => p.id === id);
      return {
        ...providerDetailFor("zh"),
        id,
        display_name: item?.display_name ?? id,
        status: item?.status ?? "unconfigured",
      };
    }

    beforeEach(() => {
      vi.mocked(API.getProviders).mockResolvedValue({ providers: groupedCatalog });
      vi.mocked(API.getProviderConfig).mockImplementation((id) => Promise.resolve(detailByProviderId(id)));
    });

    it("groups multi-channel vendors into one rail item with aggregated key count and opens primary channel on click", async () => {
      const { location } = renderAt("/app/settings?section=providers&provider=gemini-aistudio");
      const nav = await screen.findByRole("navigation", { name: "供应商列表" });
      const presetTab = within(nav).getByRole("tab", { name: /预置/ });
      expect(presetTab).toHaveTextContent("3");

      const panel = within(nav).getByRole("tabpanel");
      const links = within(panel).getAllByRole("link");
      expect(links).toHaveLength(3);

      const geminiLink = within(panel).getByRole("link", { name: /Google Gemini/ });
      expect(geminiLink).toHaveAttribute("aria-current", "page");
      expect(geminiLink).toHaveTextContent("已配置 1 个密钥");

      const arkLink = within(panel).getByRole("link", { name: /火山方舟/ });
      expect(arkLink).toHaveTextContent("已配置 2 个密钥");
      expect(within(panel).getByRole("link", { name: /Grok/ })).toHaveTextContent("未配置");

      fireEvent.click(arkLink);
      await waitFor(() => expect(location.history.at(-1)).toBe("/app/settings?section=providers&provider=ark"));
      await screen.findByRole("heading", { name: "火山方舟" });
    });

    it("activates the vendor group and matching channel tab on deep link, and stays on current channel when clicking active rail item", async () => {
      const { location } = renderAt("/app/settings?section=providers&provider=ark-agent-plan");
      await screen.findByRole("heading", { name: "火山方舟 Agent Plan" });

      const nav = screen.getByRole("navigation", { name: "供应商列表" });
      const arkLink = within(within(nav).getByRole("tabpanel")).getByRole("link", { name: /火山方舟/ });
      expect(arkLink).toHaveAttribute("aria-current", "page");

      const channelTabs = screen.getByRole("tablist", { name: "接入渠道" });
      const paygTab = within(channelTabs).getByRole("tab", { name: /按量付费/ });
      const agentPlanTab = within(channelTabs).getByRole("tab", { name: /Agent Plan/ });
      expect(agentPlanTab).toHaveAttribute("aria-selected", "true");
      expect(agentPlanTab).toHaveAccessibleName(/已配置 2 个密钥/);
      expect(paygTab).toHaveAttribute("aria-selected", "false");

      fireEvent.click(arkLink);
      expect(location.history.at(-1)).toBe("/app/settings?section=providers&provider=ark-agent-plan");

      fireEvent.click(paygTab);
      await waitFor(() => expect(location.history.at(-1)).toBe("/app/settings?section=providers&provider=ark"));
      await screen.findByRole("heading", { name: "火山方舟" });
    });

    it("guards unsaved edits when switching between channels of the same vendor", async () => {
      const { location } = renderAt("/app/settings?section=providers&provider=ark");
      await screen.findByRole("heading", { name: "火山方舟" });
      fireEvent.change(screen.getByRole("spinbutton", { name: "Max Workers" }), { target: { value: "9" } });

      const channelTabs = screen.getByRole("tablist", { name: "接入渠道" });
      fireEvent.click(within(channelTabs).getByRole("tab", { name: /Agent Plan/ }));

      const dialog = await screen.findByRole("alertdialog", { name: "有未保存的修改" });
      expect(location.history.at(-1)).toBe("/app/settings?section=providers&provider=ark");

      fireEvent.click(within(dialog).getByRole("button", { name: "放弃修改" }));
      await waitFor(() =>
        expect(location.history.at(-1)).toBe("/app/settings?section=providers&provider=ark-agent-plan"),
      );
      await screen.findByRole("heading", { name: "火山方舟 Agent Plan" });
    });
  });
});
