import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import { API, ApiRequestError } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { createDeferred } from "@/test/deferred";
import type { MarketEntry, MarketSourceInfo } from "@/types";
import { MarketSection } from "./MarketSection";

const RECENT = new Date(Date.now() - 13 * 60_000).toISOString();

function makeSource(overrides: Partial<MarketSourceInfo> = {}): MarketSourceInfo {
  return {
    id: 1,
    kind: "official",
    display_name: "ArcReel Market",
    address: "ArcReel/arcreel-market",
    index_url: "https://raw.githubusercontent.com/ArcReel/arcreel-market/HEAD/arcreel-market.json",
    canonical_key: "github:ArcReel/arcreel-market@HEAD",
    is_enabled: true,
    position: 0,
    status: "ok",
    last_error: null,
    fetched_at: RECENT,
    created_at: RECENT,
    updated_at: RECENT,
    entry_count: 3,
    index: { name: "ArcReel Market", description: null, homepage: "https://github.com/ArcReel/arcreel-market" },
    ...overrides,
  };
}

const OFFICIAL = makeSource();
const TEAM = makeSource({
  id: 2,
  kind: "custom",
  display_name: "团队市场",
  address: "someone/market",
  canonical_key: "github:someone/market@HEAD",
  position: 1,
  status: "unreachable",
  last_error: "HTTP 404",
  entry_count: 2,
  index: { name: "团队市场", description: null, homepage: null },
});
const DISABLED = makeSource({
  id: 3,
  kind: "custom",
  display_name: "停用的源",
  address: "other/market",
  canonical_key: "github:other/market@HEAD",
  position: 2,
  is_enabled: false,
  status: "never_fetched",
  fetched_at: null,
  entry_count: 0,
  index: null,
});

function makeEntry(overrides: Partial<MarketEntry> = {}): MarketEntry {
  return {
    source_id: 1,
    source_display_name: "ArcReel Market",
    type: "endpoint",
    slug: "alpha",
    path: "endpoints/alpha/definition.json",
    name: "Alpha Video",
    author: "ArcReel",
    version: "1.2.0",
    media_type: "video",
    description: "官方的 Alpha 视频接口。",
    homepage: null,
    icon: null,
    min_app_version: null,
    min_app_version_satisfied: true,
    ...overrides,
  };
}

const ENTRIES: MarketEntry[] = [
  makeEntry(),
  makeEntry({ slug: "zeta", name: "Zeta Gateway", author: "Kaze Studio", description: "通用网关协议。" }),
  makeEntry({
    source_id: 2,
    source_display_name: "团队市场",
    name: "Alpha 团队版",
    author: "someone",
    version: "0.3.0",
    description: "经内网转发。",
  }),
];

function cardNames(): string[] {
  return screen.queryAllByRole("article").map((card) => card.getAttribute("aria-label") ?? "");
}

async function openManager() {
  await userEvent.click(await screen.findByRole("button", { name: "管理市场源" }));
  return screen.findByRole("dialog", { name: "管理市场源" });
}

function row(dialog: HTMLElement, name: string): HTMLElement {
  const input = within(dialog).getByRole("textbox", { name: `${name} 的显示名` });
  const li = input.closest("li");
  if (!li) throw new Error(`row ${name} not found`);
  return li;
}

describe("MarketSection", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "listMarketSources").mockResolvedValue({ sources: [OFFICIAL, TEAM, DISABLED] });
    vi.spyOn(API, "refreshMarketSources").mockResolvedValue({ sources: [] });
    vi.spyOn(API, "listMarketEntries").mockResolvedValue({ entries: ENTRIES, app_version: "0.30.0" });
    vi.spyOn(API, "getMarketEntryIcon").mockRejectedValue(new Error("no icon"));
  });

  it("counts listed entries and enabled sources in the hero kicker", async () => {
    render(<MarketSection />);

    expect(await screen.findByText("Market · 3 endpoints from 2 sources")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "市场", level: 2 })).toBeInTheDocument();
  });

  it("renders cached entries first, then reloads them after the stale-only background refresh", async () => {
    const refreshed = makeSource({ fetched_at: new Date().toISOString() });
    const deferredRefresh = createDeferred<{ sources: MarketSourceInfo[] }>();
    vi.mocked(API.refreshMarketSources).mockReturnValue(deferredRefresh.promise);
    vi.mocked(API.listMarketEntries)
      .mockResolvedValueOnce({ entries: ENTRIES.slice(0, 1), app_version: "0.30.0" })
      .mockResolvedValue({ entries: ENTRIES, app_version: "0.30.0" });

    const { unmount } = render(<MarketSection />);

    expect(await screen.findByText("Market · 1 endpoint from 2 sources")).toBeInTheDocument();
    expect(API.refreshMarketSources).toHaveBeenCalledWith({
      staleOnly: true,
      signal: expect.any(AbortSignal),
    });
    const signal = vi.mocked(API.refreshMarketSources).mock.calls[0][0]?.signal;

    deferredRefresh.resolve({ sources: [refreshed] });
    expect(await screen.findByText("Market · 3 endpoints from 2 sources")).toBeInTheDocument();
    expect(API.listMarketEntries).toHaveBeenCalledTimes(2);

    unmount();
    expect(signal?.aborted).toBe(true);
  });

  it("lays out entries of all sources ungrouped in API order with author, version and source chip", async () => {
    render(<MarketSection />);

    await screen.findAllByRole("article");
    expect(cardNames()).toEqual(["Alpha Video", "Zeta Gateway", "Alpha 团队版"]);

    const team = screen.getByRole("article", { name: "Alpha 团队版" });
    expect(within(team).getByRole("heading", { name: "Alpha 团队版" })).toBeInTheDocument();
    expect(within(team).getByText("someone · v0.3.0")).toBeInTheDocument();
    expect(within(team).getByText("经内网转发。")).toBeInTheDocument();
    expect(within(team).getByText("团队市场")).toBeInTheDocument();
    expect(within(screen.getByRole("article", { name: "Alpha Video" })).getByText("ArcReel Market")).toBeInTheDocument();
  });

  it("filters entries by name, author or description as the user types", async () => {
    render(<MarketSection />);
    await screen.findAllByRole("article");
    const search = screen.getByRole("searchbox", { name: "搜索市场条目" });

    await userEvent.type(search, "kaze");
    expect(cardNames()).toEqual(["Zeta Gateway"]);

    await userEvent.clear(search);
    await userEvent.type(search, "内网");
    expect(cardNames()).toEqual(["Alpha 团队版"]);

    await userEvent.clear(search);
    await userEvent.type(search, "nothing-like-this");
    expect(screen.getByText("没有匹配的条目")).toBeInTheDocument();
  });

  it("offers one pressed source chip per enabled source and hides entries of deselected sources", async () => {
    render(<MarketSection />);
    await screen.findAllByRole("article");
    const group = screen.getByRole("group", { name: "按来源筛选" });

    const chips = within(group).getAllByRole("button");
    expect(chips.map((chip) => chip.textContent)).toEqual(["ArcReel Market", "团队市场"]);
    expect(chips.every((chip) => chip.getAttribute("aria-pressed") === "true")).toBe(true);
    expect(within(chips[1]).getByRole("img", { name: "无法访问" })).toBeInTheDocument();

    await userEvent.click(chips[0]);
    expect(chips[0]).toHaveAttribute("aria-pressed", "false");
    expect(cardNames()).toEqual(["Alpha 团队版"]);

    await userEvent.click(chips[0]);
    expect(cardNames()).toHaveLength(3);
  });

  it("shows endpoint as the only available entry type and keeps the installed-only switch inert", async () => {
    render(<MarketSection />);
    const types = within(await screen.findByRole("group", { name: "条目类型" })).getAllByRole("button");

    expect(types.map((type) => type.textContent)).toEqual(["调用端点", "提示词即将推出", "风格模板即将推出"]);
    expect(types[0]).toHaveAttribute("aria-pressed", "true");
    expect(types[1]).toBeDisabled();
    expect(types[2]).toBeDisabled();
    expect(screen.getByRole("switch", { name: "仅已安装" })).toBeDisabled();
  });

  it("warns about each failing enabled source with its status, error and snapshot age", async () => {
    const broken = makeSource({
      id: 4,
      kind: "custom",
      display_name: "坏索引",
      status: "invalid_index",
      last_error: null,
      fetched_at: null,
    });
    const disabledFailing = makeSource({ ...DISABLED, id: 5, display_name: "停用且失败", status: "unreachable" });
    vi.mocked(API.listMarketSources).mockResolvedValue({ sources: [OFFICIAL, TEAM, DISABLED, broken, disabledFailing] });

    render(<MarketSection />);

    const banner = await screen.findByRole("status");
    const lines = within(banner)
      .getAllByText((_, element) => element?.parentElement === banner)
      .map((line) => line.textContent);
    expect(lines).toEqual(["团队市场：无法访问 · HTTP 404，显示上次成功刷新（13分钟前）的快照", "坏索引：索引无效"]);
  });

  it("shows no banner when every enabled source is fine", async () => {
    vi.mocked(API.listMarketSources).mockResolvedValue({ sources: [OFFICIAL, DISABLED] });
    render(<MarketSection />);

    await screen.findAllByRole("article");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("says so when enabled sources have no entries at all", async () => {
    vi.mocked(API.listMarketEntries).mockResolvedValue({ entries: [], app_version: "0.30.0" });
    render(<MarketSection />);

    expect(await screen.findByText("启用的市场源里还没有条目")).toBeInTheDocument();
  });

  it("links the contribute card to the official contribution guide", async () => {
    render(<MarketSection />);

    expect(await screen.findByRole("link", { name: "阅读投稿指引" })).toHaveAttribute(
      "href",
      "https://github.com/ArcReel/arcreel-market/blob/main/CONTRIBUTING.md",
    );
  });

  it("reloads entries after a source is disabled in the manager", async () => {
    vi.spyOn(API, "updateMarketSource").mockImplementation(async (id, patch) => ({
      ...TEAM,
      id,
      ...patch,
      updated_at: new Date().toISOString(),
    }));
    render(<MarketSection />);
    await screen.findAllByRole("article");
    vi.mocked(API.listMarketEntries).mockResolvedValue({ entries: ENTRIES.slice(0, 2), app_version: "0.30.0" });
    const dialog = await openManager();

    await userEvent.click(within(dialog).getByRole("switch", { name: "启用 团队市场" }));

    await waitFor(() => expect(cardNames()).toEqual(["Alpha Video", "Zeta Gateway"]));
  });

  it("hides a disabled source from cached entries when reloading entries fails", async () => {
    vi.spyOn(API, "updateMarketSource").mockResolvedValue({
      ...TEAM,
      is_enabled: false,
      updated_at: new Date().toISOString(),
    });
    render(<MarketSection />);
    await screen.findAllByRole("article");
    vi.mocked(API.listMarketEntries).mockRejectedValue(new Error("reload failed"));
    const dialog = await openManager();

    await userEvent.click(within(dialog).getByRole("switch", { name: "启用 团队市场" }));

    await waitFor(() => expect(cardNames()).toEqual(["Alpha Video", "Zeta Gateway"]));
  });

  it("lists sources in order with status, last refresh, error and official marking", async () => {
    render(<MarketSection />);
    const dialog = await openManager();

    const names = within(dialog)
      .getAllByRole("textbox", { name: /的显示名$/ })
      .map((input) => (input as HTMLInputElement).value);
    expect(names).toEqual(["ArcReel Market", "团队市场", "停用的源"]);

    const official = row(dialog, "ArcReel Market");
    expect(within(official).getByText("官方")).toBeInTheDocument();
    expect(within(official).getByText(/正常 · 上次成功刷新 13分钟前/)).toBeInTheDocument();
    expect(within(official).getByRole("button", { name: "删除 ArcReel Market" })).toBeDisabled();
    expect(within(official).getByRole("link", { name: "打开 ArcReel Market 的主页" })).toHaveAttribute(
      "href",
      "https://github.com/ArcReel/arcreel-market",
    );

    const team = row(dialog, "团队市场");
    expect(within(team).getByText(/无法访问 · 上次成功刷新 13分钟前/)).toBeInTheDocument();
    expect(within(team).getByText("· HTTP 404")).toBeInTheDocument();
    expect(within(team).getByRole("img", { name: "无法访问" })).toBeInTheDocument();

    const disabled = row(dialog, "停用的源");
    expect(within(disabled).getByText(/尚未刷新 · 上次成功刷新 从未/)).toBeInTheDocument();
    expect(within(disabled).getByRole("switch", { name: "启用 停用的源" })).not.toBeChecked();
    expect(within(disabled).getByRole("button", { name: "刷新 停用的源" })).toBeDisabled();

    expect(
      within(dialog).getByText(/该市场源由第三方维护，其中的内容未经 ArcReel 审核。安装前请先确认来源可信。/),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("添加第三方市场源")).toBeInTheDocument();
  });

  it("adds a source and appends the returned row", async () => {
    const added = makeSource({ id: 4, kind: "custom", display_name: "新源", position: 3 });
    const add = vi.spyOn(API, "addMarketSource").mockResolvedValue(added);
    render(<MarketSection />);
    const dialog = await openManager();

    await userEvent.type(within(dialog).getByRole("textbox", { name: "市场源地址" }), " new/market ");
    await userEvent.click(within(dialog).getByRole("button", { name: "添加" }));

    expect(add).toHaveBeenCalledWith({ address: "new/market" });
    expect(await within(dialog).findByRole("textbox", { name: "新源 的显示名" })).toBeInTheDocument();
    expect(within(dialog).getByRole("textbox", { name: "市场源地址" })).toHaveValue("");
  });

  it("shows why adding a source was rejected and keeps the address", async () => {
    vi.spyOn(API, "addMarketSource").mockRejectedValue(
      new ApiRequestError("无法添加市场源：无法访问（HTTP 404）", undefined, 422),
    );
    render(<MarketSection />);
    const dialog = await openManager();

    await userEvent.type(within(dialog).getByRole("textbox", { name: "市场源地址" }), "bad/market");
    await userEvent.click(within(dialog).getByRole("button", { name: "添加" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("无法添加市场源：无法访问（HTTP 404）");
    expect(within(dialog).getByRole("textbox", { name: "市场源地址" })).toHaveValue("bad/market");
  });

  it("renames inline on Enter and toggles enablement", async () => {
    const update = vi
      .spyOn(API, "updateMarketSource")
      .mockImplementation(async (id, patch) => ({ ...TEAM, id, ...patch }));
    render(<MarketSection />);
    const dialog = await openManager();

    const input = within(dialog).getByRole("textbox", { name: "团队市场 的显示名" });
    await userEvent.clear(input);
    await userEvent.type(input, "同事的源{Enter}");
    expect(update).toHaveBeenCalledWith(2, { display_name: "同事的源" });
    expect(await within(dialog).findByRole("textbox", { name: "同事的源 的显示名" })).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("switch", { name: "启用 同事的源" }));
    expect(update).toHaveBeenLastCalledWith(2, { is_enabled: false });
  });

  it("keeps concurrent rename and toggle results when their responses arrive out of order", async () => {
    const rename = createDeferred<MarketSourceInfo>();
    const toggle = createDeferred<MarketSourceInfo>();
    vi.spyOn(API, "updateMarketSource").mockImplementation((_id, patch) =>
      "display_name" in patch ? rename.promise : toggle.promise,
    );
    render(<MarketSection />);
    const dialog = await openManager();

    const input = within(dialog).getByRole("textbox", { name: "团队市场 的显示名" });
    await userEvent.clear(input);
    await userEvent.type(input, "同事的源{Enter}");
    await userEvent.click(within(dialog).getByRole("switch", { name: "启用 同事的源" }));

    toggle.resolve({ ...TEAM, is_enabled: false });
    rename.resolve({ ...TEAM, display_name: "同事的源", is_enabled: true });

    const renamed = await within(dialog).findByRole("textbox", { name: "同事的源 的显示名" });
    await waitFor(() =>
      expect(within(dialog).getByRole("switch", { name: "启用 同事的源" })).not.toBeChecked(),
    );
    expect(renamed).toHaveValue("同事的源");
  });

  it("sends repeated toggles of one source in order and keeps the last intent", async () => {
    const off = createDeferred<MarketSourceInfo>();
    const on = createDeferred<MarketSourceInfo>();
    const update = vi
      .spyOn(API, "updateMarketSource")
      .mockReturnValueOnce(off.promise)
      .mockReturnValueOnce(on.promise);
    render(<MarketSection />);
    const dialog = await openManager();
    const toggle = () => within(dialog).getByRole("switch", { name: "启用 团队市场" });

    await userEvent.click(toggle());
    await userEvent.click(toggle());
    expect(update).toHaveBeenCalledTimes(1);
    expect(toggle()).toBeChecked();

    on.resolve({ ...TEAM, is_enabled: true });
    off.resolve({ ...TEAM, is_enabled: false });

    await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
    expect(update).toHaveBeenLastCalledWith(2, { is_enabled: true });
    await waitFor(() => expect(toggle()).toBeChecked());
  });

  it("restores the last confirmed value when every queued toggle fails", async () => {
    const off = createDeferred<MarketSourceInfo>();
    const on = createDeferred<MarketSourceInfo>();
    vi.spyOn(API, "updateMarketSource")
      .mockReturnValueOnce(off.promise)
      .mockReturnValueOnce(on.promise);
    render(<MarketSection />);
    const dialog = await openManager();
    const toggle = () => within(dialog).getByRole("switch", { name: "启用 团队市场" });

    await userEvent.click(toggle());
    await userEvent.click(toggle());
    off.reject(new Error("offline"));
    await waitFor(() => expect(API.updateMarketSource).toHaveBeenCalledTimes(2));
    on.reject(new Error("offline"));

    await waitFor(() => expect(useAppStore.getState().toast?.text).toContain("offline"));
    expect(toggle()).toBeChecked();
  });

  it("rolls back only the failed field when a concurrent update fails", async () => {
    const rename = createDeferred<MarketSourceInfo>();
    const toggle = createDeferred<MarketSourceInfo>();
    vi.spyOn(API, "updateMarketSource").mockImplementation((_id, patch) =>
      "display_name" in patch ? rename.promise : toggle.promise,
    );
    render(<MarketSection />);
    const dialog = await openManager();

    const input = within(dialog).getByRole("textbox", { name: "团队市场 的显示名" });
    await userEvent.clear(input);
    await userEvent.type(input, "同事的源{Enter}");
    await userEvent.click(within(dialog).getByRole("switch", { name: "启用 同事的源" }));

    toggle.resolve({ ...TEAM, is_enabled: false });
    rename.reject(new Error("rename failed"));

    await within(dialog).findByRole("textbox", { name: "团队市场 的显示名" });
    expect(within(dialog).getByRole("switch", { name: "启用 团队市场" })).not.toBeChecked();
  });

  it("reverts a blank display name without saving", async () => {
    const update = vi.spyOn(API, "updateMarketSource");
    render(<MarketSection />);
    const dialog = await openManager();

    const input = within(dialog).getByRole("textbox", { name: "团队市场 的显示名" });
    await userEvent.clear(input);
    await userEvent.tab();

    expect(input).toHaveValue("团队市场");
    expect(update).not.toHaveBeenCalled();
  });

  it("deletes a custom source", async () => {
    const remove = vi.spyOn(API, "deleteMarketSource").mockResolvedValue(undefined);
    render(<MarketSection />);
    const dialog = await openManager();

    await userEvent.click(within(dialog).getByRole("button", { name: "删除 团队市场" }));

    expect(remove).toHaveBeenCalledWith(2);
    await waitFor(() =>
      expect(within(dialog).queryByRole("textbox", { name: "团队市场 的显示名" })).not.toBeInTheDocument(),
    );
  });

  it("refreshes one source and shows its new status", async () => {
    vi.spyOn(API, "refreshMarketSource").mockResolvedValue({ ...TEAM, status: "invalid_index", last_error: "bad slug" });
    render(<MarketSection />);
    const dialog = await openManager();

    await userEvent.click(within(dialog).getByRole("button", { name: "刷新 团队市场" }));

    expect(API.refreshMarketSource).toHaveBeenCalledWith(2);
    const team = row(dialog, "团队市场");
    expect(await within(team).findByText(/索引无效 · 上次成功刷新/)).toBeInTheDocument();
    expect(within(team).getByText("· bad slug")).toBeInTheDocument();
  });

  it("keeps a rename made while a refresh of the same source is in flight", async () => {
    const refresh = createDeferred<MarketSourceInfo>();
    vi.spyOn(API, "refreshMarketSource").mockReturnValue(refresh.promise);
    vi.spyOn(API, "updateMarketSource").mockImplementation(async (id, patch) => ({ ...TEAM, id, ...patch }));
    render(<MarketSection />);
    const dialog = await openManager();

    await userEvent.click(within(dialog).getByRole("button", { name: "刷新 团队市场" }));
    const input = within(dialog).getByRole("textbox", { name: "团队市场 的显示名" });
    await userEvent.clear(input);
    await userEvent.type(input, "同事的源{Enter}");
    await within(dialog).findByRole("textbox", { name: "同事的源 的显示名" });

    refresh.resolve({ ...TEAM, status: "invalid_index", last_error: "bad slug" });

    expect(await within(dialog).findByText("· bad slug")).toBeInTheDocument();
    expect(row(dialog, "同事的源")).toHaveTextContent("bad slug");
  });

  it("keeps an enablement change made while saving a new order", async () => {
    const reorder = createDeferred<{ sources: MarketSourceInfo[] }>();
    vi.spyOn(API, "reorderMarketSources").mockReturnValue(reorder.promise);
    vi.spyOn(API, "updateMarketSource").mockImplementation(async (id, patch) => ({ ...TEAM, id, ...patch }));
    render(<MarketSection />);
    const dialog = await openManager();

    fireEvent.keyDown(within(dialog).getByRole("button", { name: /调整 团队市场 的顺序/ }), { key: "ArrowUp" });
    await userEvent.click(within(dialog).getByRole("switch", { name: "启用 团队市场" }));
    await waitFor(() => expect(within(dialog).getByRole("switch", { name: "启用 团队市场" })).not.toBeChecked());

    reorder.resolve({ sources: [{ ...TEAM, position: 0 }, { ...OFFICIAL, position: 1 }, DISABLED] });

    await waitFor(() =>
      expect(
        within(dialog)
          .getAllByRole("textbox", { name: /的显示名$/ })
          .map((input) => (input as HTMLInputElement).value),
      ).toEqual(["团队市场", "ArcReel Market", "停用的源"]),
    );
    expect(within(dialog).getByRole("switch", { name: "启用 团队市场" })).not.toBeChecked();
  });

  it("refreshes all enabled sources from the dialog", async () => {
    render(<MarketSection />);
    const dialog = await openManager();
    vi.mocked(API.refreshMarketSources).mockResolvedValue({
      sources: [{ ...OFFICIAL }, { ...TEAM, status: "ok", last_error: null }],
    });

    await userEvent.click(within(dialog).getByRole("button", { name: "全部刷新" }));

    expect(API.refreshMarketSources).toHaveBeenLastCalledWith();
    expect(await within(row(dialog, "团队市场")).findByText(/正常 · 上次成功刷新/)).toBeInTheDocument();
  });

  it("reorders with the keyboard on the drag handle", async () => {
    const reorder = vi
      .spyOn(API, "reorderMarketSources")
      .mockResolvedValue({ sources: [TEAM, OFFICIAL, DISABLED] });
    render(<MarketSection />);
    const dialog = await openManager();

    fireEvent.keyDown(within(dialog).getByRole("button", { name: /调整 团队市场 的顺序/ }), { key: "ArrowUp" });

    expect(reorder).toHaveBeenCalledWith([2, 1, 3]);
    await waitFor(() =>
      expect(
        within(dialog)
          .getAllByRole("textbox", { name: /的显示名$/ })
          .map((input) => (input as HTMLInputElement).value),
      ).toEqual(["团队市场", "ArcReel Market", "停用的源"]),
    );
  });

  it("keeps the latest order when an earlier reorder request fails after it", async () => {
    const first = createDeferred<{ sources: MarketSourceInfo[] }>();
    const second = createDeferred<{ sources: MarketSourceInfo[] }>();
    vi.spyOn(API, "reorderMarketSources")
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    render(<MarketSection />);
    const dialog = await openManager();
    const names = () =>
      within(dialog)
        .getAllByRole("textbox", { name: /的显示名$/ })
        .map((input) => (input as HTMLInputElement).value);

    fireEvent.keyDown(within(dialog).getByRole("button", { name: /调整 团队市场 的顺序/ }), { key: "ArrowUp" });
    await waitFor(() => expect(names()).toEqual(["团队市场", "ArcReel Market", "停用的源"]));
    fireEvent.keyDown(within(dialog).getByRole("button", { name: /调整 停用的源 的顺序/ }), { key: "ArrowUp" });
    await waitFor(() => expect(names()).toEqual(["团队市场", "停用的源", "ArcReel Market"]));

    second.resolve({
      sources: [
        { ...TEAM, position: 0 },
        { ...DISABLED, position: 1 },
        { ...OFFICIAL, position: 2 },
      ],
    });
    first.reject(new Error("stale reorder failed"));

    await waitFor(() => expect(API.reorderMarketSources).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(useAppStore.getState().toast?.text).toContain("stale reorder failed"));
    expect(names()).toEqual(["团队市场", "停用的源", "ArcReel Market"]);
  });

  it("reorders by drag and drop and restores the order when saving fails", async () => {
    vi.spyOn(API, "reorderMarketSources").mockRejectedValue(new Error("boom"));
    render(<MarketSection />);
    const dialog = await openManager();

    fireEvent.dragStart(row(dialog, "停用的源"), { dataTransfer: { effectAllowed: "none" } });
    fireEvent.drop(row(dialog, "ArcReel Market"));

    expect(API.reorderMarketSources).toHaveBeenCalledWith([3, 1, 2]);
    await waitFor(() =>
      expect(
        within(dialog)
          .getAllByRole("textbox", { name: /的显示名$/ })
          .map((input) => (input as HTMLInputElement).value),
      ).toEqual(["ArcReel Market", "团队市场", "停用的源"]),
    );
  });

});
