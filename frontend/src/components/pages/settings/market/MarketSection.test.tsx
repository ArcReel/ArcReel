import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import { API, ApiRequestError } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { MarketSourceInfo } from "@/types";
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
  });

  it("counts entries and sources of enabled sources in the hero kicker", async () => {
    render(<MarketSection />);

    expect(await screen.findByText("Market · 5 endpoints from 2 sources")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "市场" })).toBeInTheDocument();
  });

  it("renders the cached list first, then applies the stale-only background refresh", async () => {
    const refreshed = makeSource({ fetched_at: new Date().toISOString(), entry_count: 7 });
    vi.mocked(API.refreshMarketSources).mockResolvedValue({ sources: [refreshed] });

    render(<MarketSection />);

    expect(await screen.findByText("Market · 9 endpoints from 2 sources")).toBeInTheDocument();
    expect(API.refreshMarketSources).toHaveBeenCalledWith({ staleOnly: true });
  });

  it("lists sources in order with status, last refresh, error and official marking", async () => {
    render(<MarketSection />);
    const dialog = await openManager();

    const names = within(dialog)
      .getAllByRole("textbox", { name: /的显示名$/ })
      .map((input) => (input as HTMLInputElement).value);
    expect(names).toEqual(["ArcReel Market", "团队市场", "停用的源"]);

    const official = row(dialog, "ArcReel Market");
    expect(within(official).getByText("Official")).toBeInTheDocument();
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
