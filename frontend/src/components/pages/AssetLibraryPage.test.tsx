import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { createDeferred } from "@/test/deferred";
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
  connected = true;
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

/**
 * 滚到底触发下一页。findBy 在 DOM 更新后即返回，此时哨兵的 effect 可能还没挂上观察器，
 * 直接触发会落空；加载中观察器都已断开，所以有已连接的观察器即说明哨兵已就绪。
 */
async function reachBottomWhenArmed() {
  await waitFor(() => expect(FakeIntersectionObserver.instances.some((o) => o.connected)).toBe(true));
  act(() => FakeIntersectionObserver.reachBottom());
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

  it("removes renamed assets that no longer match the search and updates all counts", async () => {
    const user = userEvent.setup();
    const asset = makeAsset();
    vi.spyOn(API, "listAssets").mockResolvedValue(page([asset]));
    vi.spyOn(API, "updateAsset").mockResolvedValue({ asset: { ...asset, name: "李小红" } });
    renderPage("/app/assets?q=王");
    await user.click(await screen.findByRole("button", { name: "王小明" }));
    await user.click(screen.getByRole("button", { name: "编辑" }));
    const name = await screen.findByLabelText("名称");
    await user.clear(name);
    await user.type(name, "李小红");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByText("没有名称包含「王」的角色")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /角色\s*0/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "李小红" })).not.toBeInTheDocument();
  });

  it("does not reopen the details of an asset that left the results once it matches again", async () => {
    const user = userEvent.setup();
    const asset = makeAsset();
    const renamed = { ...asset, name: "李小红" };
    vi.spyOn(API, "listAssets").mockImplementation(async (params) => page(params?.q ? [asset] : [renamed]));
    vi.spyOn(API, "updateAsset").mockResolvedValue({ asset: renamed });
    const location = renderPage("/app/assets?q=王");
    await user.click(await screen.findByRole("button", { name: "王小明" }));
    await user.click(screen.getByRole("button", { name: "编辑" }));
    const name = await screen.findByLabelText("名称");
    await user.clear(name);
    await user.type(name, "李小红");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("没有名称包含「王」的角色");

    act(() => location.navigate("/app/assets"));
    await screen.findByText("李小红");
    expect(screen.queryByRole("dialog", { name: /李小红/ })).not.toBeInTheDocument();
  });

  it("ignores a late save for an asset that left the list after an external search change", async () => {
    const user = userEvent.setup();
    const asset = makeAsset();
    const other = makeAsset({ id: "a2", name: "李四" });
    const pending = createDeferred<{ asset: Asset }>();
    vi.spyOn(API, "listAssets").mockImplementation(async (params) => (params?.q === "李" ? page([other]) : page([asset])));
    vi.spyOn(API, "updateAsset").mockReturnValue(pending.promise);
    const location = renderPage("/app/assets?q=王");
    await user.click(await screen.findByRole("button", { name: "王小明" }));
    await user.click(screen.getByRole("button", { name: "编辑" }));
    await user.type(screen.getByLabelText("描述"), "修改");
    await user.click(screen.getByRole("button", { name: "保存" }));
    act(() => location.navigate("/app/assets?q=李"));
    expect(await screen.findByRole("button", { name: "李四" })).toBeInTheDocument();
    await act(async () => pending.resolve({ asset: { ...asset, description: "白衣少年修改" } }));

    // 关闭动画在 jsdom 中不结束，Sheet 仍让背景对读屏隐藏。
    expect(screen.getByRole("tab", { name: /角色\s*1/, hidden: true })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "王小明", hidden: true })).not.toBeInTheDocument();
  });

  it("finishes pagination after duplicate rows caused by a concurrent front insert", async () => {
    const initial = Array.from({ length: 61 }, (_, i) => makeAsset({ id: `a${i}`, name: `角色${i}` }));
    let database = initial;
    const listSpy = vi.spyOn(API, "listAssets").mockImplementation(async (params) => {
      const offset = params?.offset ?? 0;
      return page(database.slice(offset, offset + 60), { total: database.length });
    });
    renderPage();
    await screen.findByRole("button", { name: "角色59" });
    database = [makeAsset({ id: "new", name: "新角色" }), ...initial];
    await reachBottomWhenArmed();
    expect(await screen.findByRole("button", { name: "角色60" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "角色59" })).toHaveLength(1);
    act(() => FakeIntersectionObserver.reachBottom());
    expect(listSpy).toHaveBeenCalledTimes(2);
  });

  it("reloads the next page from the shifted position when an asset is created while it is loading", async () => {
    const initial = Array.from({ length: 121 }, (_, i) => makeAsset({ id: `a${i}`, name: `角色${i}` }));
    const created = makeAsset({ id: "new", name: "新角色" });
    let database = initial;
    const pending = createDeferred<void>();
    const started = createDeferred<void>();
    const listSpy = vi.spyOn(API, "listAssets").mockImplementation(async (params) => {
      const offset = params?.offset ?? 0;
      if (offset === 60 && database === initial) {
        started.resolve();
        await pending.promise;
      }
      return page(database.slice(offset, offset + 60), { total: database.length });
    });
    vi.spyOn(API, "createAsset").mockImplementation(async () => {
      database = [created, ...initial];
      return { asset: created };
    });
    renderPage();
    await screen.findByText("角色59");
    await reachBottomWhenArmed();
    await act(() => started.promise);

    fireEvent.click(screen.getAllByRole("button", { name: "新增资产" })[0]);
    fireEvent.change(await screen.findByLabelText("名称"), { target: { value: "新角色" } });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await screen.findByRole("button", { name: "新角色" });
    await act(async () => pending.resolve());
    await reachBottomWhenArmed();
    await screen.findByText("角色119");
    await reachBottomWhenArmed();

    expect(await screen.findByText("角色120")).toBeInTheDocument();
    expect(listSpy.mock.calls.map(([params]) => params?.offset)).toEqual([0, 60, 61, 121]);
  });

  it("reloads the first page when an asset is created before it arrives", async () => {
    const created = makeAsset({ id: "new", name: "新角色" });
    let database = [makeAsset()];
    const pending = createDeferred<void>();
    let calls = 0;
    vi.spyOn(API, "listAssets").mockImplementation(async () => {
      const snapshot = database;
      calls += 1;
      if (calls === 1) await pending.promise;
      return page(snapshot);
    });
    vi.spyOn(API, "createAsset").mockImplementation(async () => {
      database = [created, ...database];
      return { asset: created };
    });
    renderPage();

    fireEvent.click(screen.getAllByRole("button", { name: "新增资产" })[0]);
    fireEvent.change(await screen.findByLabelText("名称"), { target: { value: "新角色" } });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await act(async () => pending.resolve());

    expect(await screen.findByText("新角色")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /角色\s*2/ })).toBeInTheDocument();
  });

  it("keeps an external search navigation instead of rewriting it with stale input", async () => {
    vi.spyOn(API, "listAssets").mockResolvedValue(page([]));
    const location = renderPage("/app/assets?q=王");
    await screen.findByText("没有名称包含「王」的角色");
    act(() => location.navigate("/app/assets?q=庙"));
    expect(await screen.findByText("没有名称包含「庙」的角色")).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "按名称搜索资产" })).toHaveValue("庙");
    expect(location.history?.at(-1)).toBe("/app/assets?q=庙");
  });

  it("waits for image uploads before returning to details and disables saving during upload", async () => {
    const user = userEvent.setup();
    const asset = makeAsset();
    const pending = createDeferred<{ asset: Asset }>();
    vi.spyOn(API, "listAssets").mockResolvedValue(page([asset]));
    vi.spyOn(API, "replaceAssetImage").mockReturnValue(pending.promise);
    renderPage();
    await user.click(await screen.findByRole("button", { name: "王小明" }));
    await user.click(screen.getByRole("button", { name: "编辑" }));
    await user.upload(screen.getByLabelText("上传图片"), new File(["image"], "new.png", { type: "image/png" }));
    await user.type(screen.getByLabelText("描述"), "修改");
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "上传图片" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "返回详情" }));
    expect(screen.getByLabelText("描述")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await act(async () => pending.resolve({ asset: { ...asset, image_path: "new.png" } }));
    const confirm = await screen.findByRole("alertdialog");
    await user.click(within(confirm).getByRole("button", { name: "放弃修改" }));
    expect(await screen.findByRole("button", { name: "编辑" })).toBeInTheDocument();
    expect(screen.queryByLabelText("描述")).not.toBeInTheDocument();
  });

  it("disables image upload while metadata is being saved", async () => {
    const user = userEvent.setup();
    const asset = makeAsset();
    const pending = createDeferred<{ asset: Asset }>();
    vi.spyOn(API, "listAssets").mockResolvedValue(page([asset]));
    vi.spyOn(API, "updateAsset").mockReturnValue(pending.promise);
    renderPage();
    await user.click(await screen.findByRole("button", { name: "王小明" }));
    await user.click(screen.getByRole("button", { name: "编辑" }));
    await user.type(screen.getByLabelText("描述"), "修改");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByRole("button", { name: "上传图片" })).toBeDisabled();
    expect(screen.getByLabelText("上传图片")).toBeDisabled();
    await act(async () => pending.resolve({ asset: { ...asset, description: "白衣少年修改" } }));
    expect(await screen.findByText("已保存")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传图片" })).toBeEnabled();
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
    await reachBottomWhenArmed();

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

  it("starts a fresh delete confirmation after a failed deletion is closed and reopened", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "listAssets").mockResolvedValue(page([makeAsset()]));
    vi.spyOn(API, "deleteAsset").mockRejectedValue(new Error("资产正在使用"));
    renderPage();
    const openConfirm = async () => {
      await user.click(await screen.findByRole("button", { name: "「王小明」的更多操作" }));
      await user.click(await screen.findByRole("menuitem", { name: "删除" }));
      return screen.findByRole("alertdialog", { name: "删除角色「王小明」？" });
    };

    const first = await openConfirm();
    await user.click(within(first).getByRole("button", { name: "删除" }));
    expect(await within(first).findByText(/资产正在使用/)).toBeInTheDocument();
    await user.click(within(first).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());

    const second = await openConfirm();
    expect(within(second).queryByText(/资产正在使用/)).not.toBeInTheDocument();
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
