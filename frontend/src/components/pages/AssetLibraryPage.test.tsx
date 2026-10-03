import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import type { Asset, AssetListPage } from "@/types/asset";
import { AssetLibraryPage } from "./AssetLibraryPage";

function makeAsset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: "a1",
    type: "character",
    name: "王小明",
    description: "白衣少年",
    voice_style: "清亮少年音",
    image_path: null,
    audio_path: null,
    source_project: "demo",
    updated_at: "2026-09-01T08:00:00Z",
    derivatives: [],
    ...overrides,
  };
}

function page(items: Asset[], overrides: Partial<AssetListPage> = {}): AssetListPage {
  return { items, total: items.length, counts: { character: items.length, scene: 0, prop: 0 }, ...overrides };
}

/** jsdom 没有 IntersectionObserver：记下观察者，由用例模拟哨兵进入可视区。 */
class FakeIntersectionObserver {
  static instances: FakeIntersectionObserver[] = [];
  private readonly callback: IntersectionObserverCallback;
  private connected = true;
  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
    FakeIntersectionObserver.instances.push(this);
  }
  observe() {}
  disconnect() {
    this.connected = false;
  }
  static reachBottom() {
    for (const observer of FakeIntersectionObserver.instances.filter((o) => o.connected)) {
      observer.callback([{ isIntersecting: true } as IntersectionObserverEntry], observer as unknown as IntersectionObserver);
    }
  }
}

function renderPage(path = "/app/assets") {
  const location = memoryLocation({ path, record: true });
  render(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <LeaveGuardProvider>
        <AssetLibraryPage />
      </LeaveGuardProvider>
    </Router>,
  );
  return location;
}

describe("AssetLibraryPage", () => {
  beforeEach(() => {
    FakeIntersectionObserver.instances = [];
    vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows match counts on every type tab, including tabs that were never opened", async () => {
    vi.spyOn(API, "listAssets").mockResolvedValue(
      page([makeAsset()], { total: 1, counts: { character: 1, scene: 12, prop: 3 } }),
    );
    renderPage();

    expect(await screen.findByRole("tab", { name: /场景\s*12/ })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tab", { name: /道具\s*3/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /角色\s*1/ })).toHaveAttribute("aria-selected", "true");
  });

  it("loads the next page when the end of the grid scrolls into view, until the total is reached", async () => {
    const first = Array.from({ length: 60 }, (_, i) => makeAsset({ id: `a${i}`, name: `角色${i}` }));
    const second = [makeAsset({ id: "a60", name: "角色60" })];
    const listSpy = vi.spyOn(API, "listAssets").mockImplementation(async (params) =>
      page(params?.offset ? second : first, { total: 61, counts: { character: 61, scene: 0, prop: 0 } }),
    );
    renderPage();

    await screen.findByRole("button", { name: "角色59" });
    act(() => FakeIntersectionObserver.reachBottom());

    expect(await screen.findByRole("button", { name: "角色60" })).toBeInTheDocument();
    expect(listSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: "character", offset: 60, limit: 60 }),
      expect.anything(),
    );
    act(() => FakeIntersectionObserver.reachBottom());
    expect(listSpy).toHaveBeenCalledTimes(2);
  });

  it("opens details from the card, and saves edits only after entering the form", async () => {
    const user = userEvent.setup();
    const asset = makeAsset();
    vi.spyOn(API, "listAssets").mockResolvedValue(page([asset]));
    const updateSpy = vi
      .spyOn(API, "updateAsset")
      .mockResolvedValue({ asset: { ...asset, description: "黑衣剑客", updated_at: "2026-09-02T08:00:00Z" } });
    renderPage();

    await user.click(await screen.findByRole("button", { name: "王小明" }));
    const sheet = await screen.findByRole("dialog", { name: "王小明" });
    expect(within(sheet).getByText("demo")).toBeInTheDocument();
    expect(within(sheet).getByText("清亮少年音")).toBeInTheDocument();
    expect(within(sheet).queryByRole("textbox")).not.toBeInTheDocument();

    await user.click(within(sheet).getByRole("button", { name: "编辑" }));
    const description = await screen.findByLabelText("描述");
    await user.clear(description);
    await user.type(description, "黑衣剑客");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith("a1", { name: "王小明", description: "黑衣剑客", voice_style: "清亮少年音" }),
    );
    await user.click(screen.getByRole("button", { name: "返回详情" }));
    expect(await within(await screen.findByRole("dialog", { name: "王小明" })).findByText("黑衣剑客")).toBeInTheDocument();
  });

  it("deletes from the card menu after confirming, and lowers the tab count", async () => {
    const user = userEvent.setup();
    const asset = makeAsset({ audio_path: "global_assets/character/voice.wav" });
    vi.spyOn(API, "listAssets").mockResolvedValue(page([asset, makeAsset({ id: "a2", name: "李小红" })]));
    const deleteSpy = vi.spyOn(API, "deleteAsset").mockResolvedValue(undefined);
    renderPage();

    await user.click(await screen.findByRole("button", { name: "「王小明」的更多操作" }));
    await user.click(await screen.findByRole("menuitem", { name: "删除" }));
    const confirm = await screen.findByRole("alertdialog", { name: "删除角色「王小明」？" });
    expect(within(confirm).getByText(/资产图与参考音频会一并删除/)).toBeInTheDocument();

    await user.click(within(confirm).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "王小明" })).not.toBeInTheDocument());
    expect(deleteSpy).toHaveBeenCalledWith("a1");
    expect(screen.getByRole("tab", { name: /角色\s*1/ })).toBeInTheDocument();
  });

  it("searches by name across types and keeps the term in the address", async () => {
    const user = userEvent.setup();
    const listSpy = vi.spyOn(API, "listAssets").mockResolvedValue(page([]));
    const location = renderPage("/app/assets?tab=scene");

    await user.type(await screen.findByRole("searchbox", { name: "按名称搜索资产" }), "庙");

    await waitFor(() =>
      expect(listSpy).toHaveBeenLastCalledWith(expect.objectContaining({ type: "scene", q: "庙" }), expect.anything()),
    );
    expect(location.history?.at(-1)).toBe("/app/assets?tab=scene&q=%E5%BA%99");
    expect(await screen.findByText("没有名称包含「庙」的场景")).toBeInTheDocument();
  });
});
