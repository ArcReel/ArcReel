import { render, screen, waitFor, within, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import i18n from "@/i18n";
import { API } from "@/api";
import { ProviderSection } from "./ProviderSection";
import type { ProviderInfo, CustomProviderInfo } from "@/types";

function renderAt(path = "/app/settings?provider=gemini-aistudio") {
  const location = memoryLocation({ path, record: true });
  return {
    ...render(
      <Router hook={location.hook} searchHook={location.searchHook}>
        <ProviderSection />
      </Router>,
    ),
    location,
  };
}

// 后端按 Accept-Language 成文供应商与模型名，这里用替身按当前语言返回对应译名。
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
        configured_keys: [],
        missing_keys: [],
        models: {},
      },
    ],
  };
}

// 自定义供应商名是用户数据、真实场景不随语言变化；这里让替身按语言分叉，
// 只为让「custom 列表随语言一起重取」可被 DOM 断言观察到。
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

describe("ProviderSection", () => {
  beforeEach(() => {
    vi.spyOn(API, "getProviders").mockImplementation(() =>
      Promise.resolve(providersFor(i18n.language)),
    );
    vi.spyOn(API, "listCustomProviders").mockImplementation(() =>
      Promise.resolve(customFor(i18n.language)),
    );
    vi.spyOn(API, "getProviderConfig").mockRejectedValue(new Error("detail not under test"));
  });

  afterEach(async () => {
    await act(async () => {
      await i18n.changeLanguage("zh");
    });
  });

  it("refetches the provider catalog when the interface language changes", async () => {
    renderAt();

    const nav = () => screen.getByRole("navigation");
    await screen.findByRole("navigation");
    expect(within(nav()).getByText("Gemini AI Studio（中文）")).toBeInTheDocument();
    expect(within(nav()).getByText("我的端点（中文）")).toBeInTheDocument();

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    // 切换语言后目录须按新语言重取，否则停留在切换前的译名
    await waitFor(() =>
      expect(within(nav()).getByText("Gemini AI Studio (EN)")).toBeInTheDocument(),
    );
    expect(within(nav()).getByText("My Endpoint (EN)")).toBeInTheDocument();
    expect(within(nav()).queryByText("Gemini AI Studio（中文）")).not.toBeInTheDocument();
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

    // 语言切换是静默刷新：不回到 loading 面板，详情面板不被卸载
    expect(screen.queryByText(/加载供应商列表|Loading providers/)).not.toBeInTheDocument();
    expect(
      within(screen.getByRole("navigation")).getByText("Gemini AI Studio（中文）"),
    ).toBeInTheDocument();

    await act(async () => {
      releaseRefetch?.();
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(
        within(screen.getByRole("navigation")).getByText("Gemini AI Studio (EN)"),
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

    // 静默刷新失败不得把整个小节换成错误面板：那会卸载详情面板、丢掉未保存的表单输入
    await waitFor(() =>
      expect(
        within(screen.getByRole("navigation")).getByText("Gemini AI Studio（中文）"),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("network down")).not.toBeInTheDocument();
  });

  it("surfaces the error when the language switches before any catalog has loaded", async () => {
    vi.spyOn(API, "getProviders").mockImplementation(() => {
      // 首次（中文）拉取永不落定：语言切换发生在它返回之前
      if (i18n.language !== "en") return new Promise<never>(() => {});
      return Promise.reject(new Error("network down"));
    });

    renderAt();

    // 首次拉取尚未返回就切语言：没有可留的目录，失败必须走错误面板而非留下空列表
    await act(async () => {
      await i18n.changeLanguage("en");
    });

    await screen.findByText("network down");
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("selects the first preset provider when the URL names no selection", async () => {
    const { location } = renderAt("/app/settings");
    const nav = await screen.findByRole("navigation");

    // 兜底选中以 replace 写回 URL，并在目录里点亮首个 preset
    await waitFor(() =>
      expect(within(nav).getByText("Gemini AI Studio（中文）").closest('[aria-current="page"]')).not.toBeNull(),
    );
    // replace 写回：历史里只剩替换后的一条，不追加
    expect(location.history).toEqual(["/app/settings?provider=gemini-aistudio"]);
  });
});
