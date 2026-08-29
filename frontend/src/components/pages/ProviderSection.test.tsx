import { render, screen, waitFor, within, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import i18n from "@/i18n";
import { API } from "@/api";
import { ProviderSection } from "./ProviderSection";
import type { ProviderInfo, CustomProviderInfo } from "@/types";

let search = "provider=gemini-aistudio";
const navigateMock = vi.fn((to: string) => {
  search = to.includes("?") ? to.slice(to.indexOf("?") + 1) : "";
});

vi.mock("wouter", () => ({
  useLocation: () => ["/app/settings", navigateMock],
  useSearch: () => search,
}));

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
    search = "provider=gemini-aistudio";
    navigateMock.mockClear();
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
    render(<ProviderSection />);

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

    render(<ProviderSection />);
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

  it("selects the first preset provider when the URL names no selection", async () => {
    search = "";

    render(<ProviderSection />);
    await screen.findByRole("navigation");

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/app/settings?provider=gemini-aistudio", {
        replace: true,
      }),
    );
  });
});
