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

// Backends render provider/model names by Accept-Language; fakes return matching current-language translations.
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

// Custom-provider names are user data, language-independent in reality. Fakes branch by language
// only to make custom-list language refetch observable through DOM assertions.
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

/** Fill/save every required New custom provider field. */
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

    // Language switches refetch catalogs in new languages, never retain previous translations.
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

    // Language changes refresh silently, without loading panels/detail unmounts.
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

    // Silent failures must not replace sections with error panels, unmounting details and losing unsaved inputs.
    await waitFor(() =>
      expect(
        within(screen.getByRole("navigation")).getAllByText("Gemini AI Studio（中文）")[0],
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("network down")).not.toBeInTheDocument();
  });

  it("surfaces the error when the language switches before any catalog has loaded", async () => {
    vi.spyOn(API, "getProviders").mockImplementation(() => {
      // Initial Chinese fetch never settles; language changes precede its return.
      if (i18n.language !== "en") return new Promise<never>(() => {});
      return Promise.reject(new Error("network down"));
    });

    renderAt();

    // Switching before initial responses leaves no catalog to retain; failures require error panels, never empty lists.
    await act(async () => {
      await i18n.changeLanguage("en");
    });

    await screen.findByText("network down");
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("selects the first preset provider when the URL names no selection", async () => {
    const { location } = renderAt("/app/settings");

    // Fallback selections replace URLs and highlight first presets.
    const panel = await screen.findByRole("tabpanel");
    await waitFor(() =>
      expect(within(panel).getByRole("link", { name: /Gemini AI Studio（中文）/ })).toHaveAttribute("aria-current", "page"),
    );
    // Replace history entries, never append.
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

    // After saves select newly created entries, or users remain on filled forms and repeat saves create duplicate providers.
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

    // Selection comes from creation responses, independent of catalog refresh results; notify refresh failures separately.
    await waitFor(() => expect(location.history.at(-1)).toBe("/app/settings?section=providers&custom=2"));
    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({
        text: "已保存，但供应商列表刷新失败，请重新加载页面",
        tone: "warning",
      }),
    );
  });

  it("opens a custom provider together with its provider multiplier", async () => {
    // Fetch multipliers/providers fully before forms: late multipliers appear external to already-started edits, persisting old values on saves.
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: [CHAT_ENDPOINT] });
    vi.spyOn(API, "getCustomProvider").mockResolvedValue(customProvider(1, "旧端点"));
    vi.mocked(API.getProviderMultiplier).mockResolvedValue({ multiplier: 0.8, default: 1 });

    renderAt("/app/settings?section=providers&custom=1");

    expect(await screen.findByRole("textbox", { name: "供应商倍率" })).toHaveValue("0.8");
    expect(API.getProviderMultiplier).toHaveBeenCalledWith("custom-1", { signal: expect.any(AbortSignal) });
  });

  it("deleting a custom provider with unsaved edits goes straight to the first preset", async () => {
    // Providers deleted: unsaved edits have nowhere to persist, so postdelete navigation bypasses leave guards.
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
});
