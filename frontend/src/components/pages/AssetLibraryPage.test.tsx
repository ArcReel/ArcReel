import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { useAssetsStore } from "@/stores/assets-store";
import { AssetLibraryPage } from "./AssetLibraryPage";

vi.mock("@/components/assets/AssetFormModal", () => ({
  AssetFormModal: () => <div data-testid="asset-form-modal" />,
}));

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

describe("AssetLibraryPage tablist (issue #488)", () => {
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

  it("ArrowRight moves focus + selection to next tab and cycles at end", () => {
    renderPage();
    const tabs = screen.getAllByRole("tab");
    tabs[0].focus();
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(tabs[1]).toHaveAttribute("aria-selected", "true");
    expect(tabs[0]).toHaveAttribute("aria-selected", "false");

    fireEvent.keyDown(tabs[1], { key: "ArrowRight" });
    expect(tabs[2]).toHaveAttribute("aria-selected", "true");

    // cycle back to first
    fireEvent.keyDown(tabs[2], { key: "ArrowRight" });
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
  });

  it("ArrowLeft moves focus + selection to previous tab and cycles at start", () => {
    renderPage();
    const tabs = screen.getAllByRole("tab");
    tabs[0].focus();
    fireEvent.keyDown(tabs[0], { key: "ArrowLeft" });
    // wraps to last
    expect(tabs[2]).toHaveAttribute("aria-selected", "true");
  });

  it("Home jumps to first tab; End jumps to last tab", () => {
    renderPage();
    const tabs = screen.getAllByRole("tab");
    fireEvent.keyDown(tabs[0], { key: "End" });
    expect(tabs[2]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(tabs[2], { key: "Home" });
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
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
