import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { useAssetsStore } from "@/stores/assets-store";
import type { Asset } from "@/types/asset";
import { AssetLibraryPage } from "./AssetLibraryPage";

function renderPage(initialPath = "/app/assets") {
  const location = memoryLocation({ path: initialPath, record: true });
  return {
    ...render(
      <Router hook={location.hook} searchHook={location.searchHook}>
        <AssetLibraryPage />
      </Router>,
    ),
    location,
  };
}

describe("AssetLibraryPage tablist", () => {
  beforeEach(() => {
    useAssetsStore.setState(useAssetsStore.getInitialState(), true);
    vi.spyOn(API, "listAssets").mockResolvedValue({ items: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders nav with role=tablist + aria-orientation + aria-label", () => {
    renderPage();
    const tablist = screen.getByRole("tablist");
    expect(tablist).toHaveAttribute("aria-orientation", "horizontal");
    expect(tablist).toHaveAttribute("aria-label", "资产类型");
  });

  it("renders three tab buttons with role=tab + aria-selected reflecting active tab", () => {
    renderPage();
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(3);
    // character active by default
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[1]).toHaveAttribute("aria-selected", "false");
    expect(tabs[2]).toHaveAttribute("aria-selected", "false");
  });

  it("applies roving tabindex (active=0, others=-1)", () => {
    renderPage();
    const [character, scene, prop] = screen.getAllByRole("tab");
    expect(character).toHaveAttribute("tabindex", "0");
    expect(scene).toHaveAttribute("tabindex", "-1");
    expect(prop).toHaveAttribute("tabindex", "-1");
  });

  it("ArrowRight moves focus + selection to next tab and cycles at end", async () => {
    renderPage();
    const tabs = screen.getAllByRole("tab");
    tabs[0].focus();
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(tabs[1]).toHaveAttribute("aria-selected", "true");
    expect(tabs[0]).toHaveAttribute("aria-selected", "false");
    // roving tabindex + focus 都是 WAI-ARIA Tabs 规范要求：激活 tab tabindex=0
    // 进 Tab 序列、其他=-1 跳过；激活 tab 必须真的拿到焦点。moveTabFocus 用
    // requestAnimationFrame 异步搬焦点，所以 focus 断言要 waitFor。
    expect(tabs[1]).toHaveAttribute("tabindex", "0");
    expect(tabs[0]).toHaveAttribute("tabindex", "-1");
    await waitFor(() => expect(tabs[1]).toHaveFocus());

    fireEvent.keyDown(tabs[1], { key: "ArrowRight" });
    expect(tabs[2]).toHaveAttribute("aria-selected", "true");
    expect(tabs[2]).toHaveAttribute("tabindex", "0");
    expect(tabs[1]).toHaveAttribute("tabindex", "-1");
    await waitFor(() => expect(tabs[2]).toHaveFocus());

    // cycle back to first
    fireEvent.keyDown(tabs[2], { key: "ArrowRight" });
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[0]).toHaveAttribute("tabindex", "0");
    expect(tabs[2]).toHaveAttribute("tabindex", "-1");
    await waitFor(() => expect(tabs[0]).toHaveFocus());
  });

  it("ArrowLeft moves focus + selection to previous tab and cycles at start", async () => {
    renderPage();
    const tabs = screen.getAllByRole("tab");
    tabs[0].focus();
    fireEvent.keyDown(tabs[0], { key: "ArrowLeft" });
    // wraps to last
    expect(tabs[2]).toHaveAttribute("aria-selected", "true");
    expect(tabs[2]).toHaveAttribute("tabindex", "0");
    expect(tabs[0]).toHaveAttribute("tabindex", "-1");
    await waitFor(() => expect(tabs[2]).toHaveFocus());
  });

  it("Home jumps to first tab; End jumps to last tab", async () => {
    renderPage();
    const tabs = screen.getAllByRole("tab");
    fireEvent.keyDown(tabs[0], { key: "End" });
    expect(tabs[2]).toHaveAttribute("aria-selected", "true");
    expect(tabs[2]).toHaveAttribute("tabindex", "0");
    expect(tabs[0]).toHaveAttribute("tabindex", "-1");
    await waitFor(() => expect(tabs[2]).toHaveFocus());

    fireEvent.keyDown(tabs[2], { key: "Home" });
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[0]).toHaveAttribute("tabindex", "0");
    expect(tabs[2]).toHaveAttribute("tabindex", "-1");
    await waitFor(() => expect(tabs[0]).toHaveFocus());
  });

  it("renders tabpanel labelled by the active tab id with stable shared id", () => {
    renderPage();
    const panel = screen.getByRole("tabpanel");
    // 所有 tab 共用同一个 panel id，避免未激活 tab 的 aria-controls
    // 指向不存在的 DOM 元素（WAI-ARIA 1.2 要求引用必须可解析）。
    expect(panel).toHaveAttribute("id", "asset-panel");
    expect(panel).toHaveAttribute("aria-labelledby", "asset-tab-character");

    // 每个 tab 的 aria-controls 都必须能在 DOM 中找到目标
    const tabs = screen.getAllByRole("tab");
    for (const tab of tabs) {
      const controls = tab.getAttribute("aria-controls");
      expect(controls).toBeTruthy();
      expect(document.getElementById(controls!)).not.toBeNull();
    }
  });

  it("respects URL ?tab=scene as initial active tab", () => {
    renderPage("/app/assets?tab=scene");
    const tabs = screen.getAllByRole("tab");
    expect(tabs[0]).toHaveAttribute("aria-selected", "false");
    expect(tabs[1]).toHaveAttribute("aria-selected", "true");
    expect(tabs[2]).toHaveAttribute("aria-selected", "false");
  });
});

describe("AssetLibraryPage 删除确认文案", () => {
  function makeAsset(overrides: Partial<Asset> = {}): Asset {
    return {
      id: "a1",
      type: "character",
      name: "王",
      description: "",
      voice_style: "",
      image_path: "characters/王.png",
      audio_path: null,
      source_project: null,
      updated_at: null,
      ...overrides,
    };
  }

  beforeEach(() => {
    useAssetsStore.setState(useAssetsStore.getInitialState(), true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("带参考音频的资产删除确认文案同时提及音频，不止图片", async () => {
    vi.spyOn(API, "listAssets").mockResolvedValue({ items: [makeAsset({ audio_path: "characters/refs_audio/王.wav" })] });
    renderPage();

    const deleteBtn = await screen.findByRole("button", { name: "删除" });
    fireEvent.click(deleteBtn);

    expect(await screen.findByText(/图片与参考音频也会被删除/)).toBeInTheDocument();
  });

  it("不带参考音频的资产删除确认文案维持原样，不提及音频", async () => {
    vi.spyOn(API, "listAssets").mockResolvedValue({ items: [makeAsset()] });
    renderPage();

    const deleteBtn = await screen.findByRole("button", { name: "删除" });
    fireEvent.click(deleteBtn);

    expect(await screen.findByText(/图片也会被删除/)).toBeInTheDocument();
    expect(screen.queryByText(/参考音频/)).not.toBeInTheDocument();
  });
});
