import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import "@/i18n";
import { API, ApiRequestError } from "@/api";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { useAppStore } from "@/stores/app-store";
import { createDeferred } from "@/test/deferred";
import type { GetSystemConfigResponse, MarketEntry, MarketSourceInfo, OfficialServiceState } from "@/types";
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
    installation: null,
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

const OFFICIAL_SERVICE_OFF: OfficialServiceState = {
  available: true,
  enabled: false,
  notice_seen: true,
  instance_id: null,
};
const OFFICIAL_SERVICE_ON: OfficialServiceState = {
  available: true,
  enabled: true,
  notice_seen: true,
  instance_id: "inst-123",
};
const OFFICIAL_SERVICE_FIRST_VISIT: OfficialServiceState = { ...OFFICIAL_SERVICE_ON, notice_seen: false };

const SYSTEM_CONFIG = {
  settings: { market_github_proxy_prefix: "https://old.example.com/" },
  options: {},
} as unknown as GetSystemConfigResponse;

function renderMarket(tab?: "shared" | "settings", extra = "") {
  const location = memoryLocation({
    path: `/app/settings?section=market${tab ? `&tab=${tab}` : ""}${extra}`,
    record: true,
  });
  render(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <LeaveGuardProvider>
        <MarketSection />
      </LeaveGuardProvider>
    </Router>,
  );
  return location;
}

function cardNames(): string[] {
  return screen.queryAllByRole("article").map((card) => within(card).getByRole("heading").textContent ?? "");
}

/** 市场源列表里的顺序，按每行拖动把手的名称读出。 */
function sourceOrder(): string[] {
  return screen
    .getAllByRole("button", { name: /^调整「.+」的顺序$/ })
    .map((handle) => handle.getAttribute("aria-label")?.replace(/^调整「(.+)」的顺序$/, "$1") ?? "");
}

function sourceRow(name: string): HTMLElement {
  const row = screen.getByRole("button", { name: `调整「${name}」的顺序` }).closest("li");
  if (!row) throw new Error(`row ${name} not found`);
  return row;
}

async function chooseSourceAction(name: string, action: string) {
  await userEvent.click(screen.getByRole("button", { name: `「${name}」的更多操作` }));
  await userEvent.click(await screen.findByRole("menuitem", { name: action }));
}

async function renameSource(name: string, next: string) {
  await chooseSourceAction(name, "重命名");
  const dialog = await screen.findByRole("dialog", { name: "重命名市场源" });
  const input = within(dialog).getByRole("textbox", { name: "显示名称" });
  await userEvent.clear(input);
  await userEvent.type(input, `${next}{Enter}`);
  await waitFor(() => expect(dialog).not.toBeInTheDocument());
}

const enableSwitch = (name: string) => screen.getByRole("switch", { name: `启用「${name}」` });

describe("MarketSection", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "listMarketSources").mockResolvedValue({ sources: [OFFICIAL, TEAM, DISABLED] });
    vi.spyOn(API, "refreshMarketSources").mockResolvedValue({ sources: [] });
    vi.spyOn(API, "listMarketEntries").mockResolvedValue({ entries: ENTRIES, app_version: "0.30.0" });
    vi.spyOn(API, "getMarketEntryIcon").mockRejectedValue(new Error("no icon"));
    vi.spyOn(API, "getOfficialService").mockResolvedValue(OFFICIAL_SERVICE_OFF);
    vi.spyOn(API, "listMarketEntryAggregates").mockResolvedValue({ items: [] });
    vi.spyOn(API, "listMarketSubmissions").mockResolvedValue({ submissions: [] });
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(SYSTEM_CONFIG);
  });

  describe("browse", () => {
    it("renders cached entries first, then reloads them after the stale-only background refresh", async () => {
      const refreshed = makeSource({ fetched_at: new Date().toISOString() });
      const deferredRefresh = createDeferred<{ sources: MarketSourceInfo[] }>();
      vi.mocked(API.refreshMarketSources).mockReturnValue(deferredRefresh.promise);
      vi.mocked(API.listMarketEntries)
        .mockResolvedValueOnce({ entries: ENTRIES.slice(0, 1), app_version: "0.30.0" })
        .mockResolvedValue({ entries: ENTRIES, app_version: "0.30.0" });

      renderMarket();

      expect(await screen.findByText("1 个调用端点")).toBeInTheDocument();
      expect(API.refreshMarketSources).toHaveBeenCalledWith({ staleOnly: true, signal: expect.any(AbortSignal) });

      deferredRefresh.resolve({ sources: [refreshed] });
      expect(await screen.findByText("3 个调用端点")).toBeInTheDocument();
      expect(API.listMarketEntries).toHaveBeenCalledTimes(2);
    });

    it("lays out entries of all sources in API order with author, version and source", async () => {
      renderMarket();

      await screen.findAllByRole("article");
      expect(cardNames()).toEqual(["Alpha Video", "Zeta Gateway", "Alpha 团队版"]);
      const team = screen.getByRole("article", { name: "Alpha 团队版" });
      expect(within(team).getByText("someone，v0.3.0")).toBeInTheDocument();
      expect(within(team).getByText("经内网转发。")).toBeInTheDocument();
      expect(within(team).getByText("团队市场")).toBeInTheDocument();
      const alpha = screen.getByRole("article", { name: "Alpha Video" });
      expect(within(alpha).getByRole("img", { name: "官方" })).toBeInTheDocument();
      // 条目类型目前只有调用端点，不显示类型筛选
      expect(screen.getByRole("group", { name: "媒体类型" })).toBeInTheDocument();
      expect(screen.queryByRole("group", { name: "条目类型" })).not.toBeInTheDocument();
    });

    it("filters entries by name, author or description as the user types", async () => {
      renderMarket();
      await screen.findAllByRole("article");
      const search = screen.getByRole("searchbox", { name: "搜索市场条目" });

      await userEvent.type(search, "kaze");
      expect(cardNames()).toEqual(["Zeta Gateway"]);
      expect(screen.getByText("1 / 3 个调用端点")).toBeInTheDocument();

      await userEvent.clear(search);
      await userEvent.type(search, "内网");
      expect(cardNames()).toEqual(["Alpha 团队版"]);

      await userEvent.clear(search);
      await userEvent.type(search, "nothing-like-this");
      expect(screen.getByText("没有匹配的条目")).toBeInTheDocument();
    });

    it("offers one pressed toggle per enabled source and hides entries of deselected sources", async () => {
      renderMarket();
      await screen.findAllByRole("article");
      const group = screen.getByRole("group", { name: "市场源" });

      const toggles = within(group).getAllByRole("button");
      expect(toggles.map((toggle) => toggle.textContent)).toEqual(["ArcReel Market", "团队市场"]);
      expect(toggles.every((toggle) => toggle.getAttribute("aria-pressed") === "true")).toBe(true);
      expect(within(toggles[1]).getByRole("img", { name: "无法访问" })).toBeInTheDocument();

      await userEvent.click(toggles[0]);
      expect(toggles[0]).toHaveAttribute("aria-pressed", "false");
      expect(cardNames()).toEqual(["Alpha 团队版"]);

      await userEvent.click(toggles[0]);
      expect(cardNames()).toHaveLength(3);
    });

    it("hides the source filter when only one market source is enabled", async () => {
      vi.mocked(API.listMarketSources).mockResolvedValue({ sources: [OFFICIAL, DISABLED] });
      renderMarket();
      await screen.findAllByRole("article");

      expect(screen.queryByRole("group", { name: "市场源" })).not.toBeInTheDocument();
    });

    it("narrows entries to one media type and restores the full list with all", async () => {
      vi.mocked(API.listMarketEntries).mockResolvedValue({
        entries: [...ENTRIES, makeEntry({ slug: "pixel", name: "Pixel Image", media_type: "image" })],
        app_version: "0.30.0",
      });
      renderMarket();
      await screen.findAllByRole("article");
      const group = screen.getByRole("group", { name: "媒体类型" });
      const [all, image, video] = within(group).getAllByRole("button");
      expect(all).toHaveAttribute("aria-pressed", "true");

      await userEvent.click(image);
      expect(image).toHaveAttribute("aria-pressed", "true");
      expect(all).toHaveAttribute("aria-pressed", "false");
      expect(cardNames()).toEqual(["Pixel Image"]);

      await userEvent.click(video);
      expect(cardNames()).toEqual(["Alpha Video", "Zeta Gateway", "Alpha 团队版"]);

      await userEvent.click(all);
      expect(cardNames()).toHaveLength(4);
    });

    it("presets the media filter from the address and leaves the address alone when it changes", async () => {
      vi.mocked(API.listMarketEntries).mockResolvedValue({
        entries: [...ENTRIES, makeEntry({ slug: "pixel", name: "Pixel Image", media_type: "image" })],
        app_version: "0.30.0",
      });
      const location = renderMarket(undefined, "&media=image");
      await screen.findAllByRole("article");
      const group = screen.getByRole("group", { name: "媒体类型" });
      const [all, image] = within(group).getAllByRole("button");
      expect(image).toHaveAttribute("aria-pressed", "true");
      expect(cardNames()).toEqual(["Pixel Image"]);

      await userEvent.click(all);
      expect(cardNames()).toHaveLength(4);
      expect(location.history?.at(-1)).toBe("/app/settings?section=market&media=image");
    });

    it("ignores a media type the market has no filter for", async () => {
      renderMarket(undefined, "&media=audio");
      await screen.findAllByRole("article");
      const [all] = within(screen.getByRole("group", { name: "媒体类型" })).getAllByRole("button");
      expect(all).toHaveAttribute("aria-pressed", "true");
    });

    it("filters by installation records, opens an installed endpoint and opens details from the card", async () => {
      const installed = {
        endpoint_id: 7,
        endpoint_key: "ce-7",
        endpoint_display_name: "Alpha",
        installed_version: "1.2.0",
        state: "current" as const,
        modified: false,
      };
      vi.mocked(API.listMarketEntries).mockResolvedValue({
        entries: [makeEntry({ installation: installed }), ENTRIES[1]],
        app_version: "0.30.0",
      });
      vi.spyOn(API, "getMarketEntry").mockRejectedValue(new Error("Preview unavailable"));
      vi.spyOn(API, "getMarketEntryDefinition").mockResolvedValue({
        definition: {},
        entry_matches_definition: false,
        definition_digest: "reviewed-digest",
      });
      vi.spyOn(API, "listCustomEndpoints").mockResolvedValue({ endpoints: [] });
      const location = renderMarket();
      await screen.findAllByRole("article");

      await userEvent.click(screen.getByRole("switch", { name: "只看已安装" }));
      expect(cardNames()).toEqual(["Alpha Video"]);

      await userEvent.click(screen.getByRole("button", { name: "Alpha Video" }));
      expect(await screen.findByRole("dialog", { name: "Alpha Video" })).toBeInTheDocument();
      expect(await screen.findByText("Preview unavailable")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "取消" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

      await userEvent.click(screen.getByRole("button", { name: "打开端点" }));
      expect(location.history?.at(-1)).toBe("/app/settings?section=endpoints&endpoint=ce-7");
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
      vi.mocked(API.listMarketSources).mockResolvedValue({
        sources: [OFFICIAL, TEAM, DISABLED, broken, disabledFailing],
      });

      renderMarket();

      const banner = await screen.findByRole("status");
      expect(within(banner).getByText("部分市场源刷新失败")).toBeInTheDocument();
      expect(within(banner).getByText("团队市场：无法访问 · HTTP 404，显示上次成功刷新（13分钟前）的快照")).toBeInTheDocument();
      expect(within(banner).getByText("坏索引：索引无效")).toBeInTheDocument();
      expect(banner).not.toHaveTextContent("停用且失败");
    });

    it("shows no banner when every enabled source is fine", async () => {
      vi.mocked(API.listMarketSources).mockResolvedValue({ sources: [OFFICIAL, DISABLED] });
      renderMarket();

      await screen.findAllByRole("article");
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });

    it("says so when enabled sources have no entries at all", async () => {
      vi.mocked(API.listMarketEntries).mockResolvedValue({ entries: [], app_version: "0.30.0" });
      renderMarket();

      expect(await screen.findByText("启用的市场源里还没有条目")).toBeInTheDocument();
    });

    it("hides entries of a source disabled in settings, even when reloading entries fails", async () => {
      vi.spyOn(API, "updateMarketSource").mockResolvedValue({ ...TEAM, is_enabled: false, updated_at: new Date().toISOString() });
      renderMarket();
      await screen.findAllByRole("article");
      vi.mocked(API.listMarketEntries).mockRejectedValue(new Error("reload failed"));

      await userEvent.click(screen.getByRole("tab", { name: "设置" }));
      await userEvent.click(enableSwitch("团队市场"));
      await userEvent.click(screen.getByRole("tab", { name: "浏览" }));

      await waitFor(() => expect(cardNames()).toEqual(["Alpha Video", "Zeta Gateway"]));
    });
  });

  describe("tabs", () => {
    it("keeps the active tab in the address", async () => {
      const location = renderMarket();
      await screen.findAllByRole("article");

      await userEvent.click(screen.getByRole("tab", { name: "设置" }));
      expect(location.history?.at(-1)).toBe("/app/settings?section=market&tab=settings");
      expect(await screen.findByRole("region", { name: "市场源" })).toBeInTheDocument();

      await userEvent.click(screen.getByRole("tab", { name: "浏览" }));
      expect(location.history?.at(-1)).toBe("/app/settings?section=market");
    });
  });

  describe("my shares", () => {
    it("explains that sharing needs the official service and links to settings", async () => {
      renderMarket("shared");

      expect(await screen.findByText("分享到官方市场需要使用官方服务。")).toBeInTheDocument();
      expect(API.listMarketSubmissions).not.toHaveBeenCalled();
      expect(screen.getByRole("link", { name: "前往市场设置" })).toHaveAttribute(
        "href",
        "/app/settings?section=market&tab=settings",
      );
      expect(screen.getByRole("link", { name: "阅读投稿指引" })).toHaveAttribute(
        "href",
        "https://github.com/ArcReel/arcreel-market/blob/main/CONTRIBUTING.md",
      );
    });

    it("lists my submissions with their endpoint, status and PR link, and counts them on the tab", async () => {
      vi.mocked(API.getOfficialService).mockResolvedValue(OFFICIAL_SERVICE_ON);
      vi.mocked(API.listMarketSubmissions).mockResolvedValue({
        submissions: [
          {
            endpoint_id: 7,
            endpoint_key: "ce-7",
            endpoint_display_name: "我的端点",
            type: "endpoint",
            slug: "my-endpoint",
            status: "closed",
            pr_url: "https://github.com/ArcReel/arcreel-market/pull/9",
            stale: true,
          },
        ],
      });
      renderMarket("shared");

      expect(await screen.findByRole("tab", { name: /^我的分享\s*1$/ })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "我的端点" })).toHaveAttribute(
        "href",
        "/app/settings?section=endpoints&endpoint=ce-7",
      );
      expect(screen.getByText("my-endpoint")).toBeInTheDocument();
      expect(screen.getByText("已拒绝")).toBeInTheDocument();
      expect(screen.getByText("暂时无法连接官方服务，显示的是上次获取的状态")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "查看 PR" })).toHaveAttribute(
        "href",
        "https://github.com/ArcReel/arcreel-market/pull/9",
      );
    });

    it("points to sharing from an endpoint when nothing has been shared", async () => {
      vi.mocked(API.getOfficialService).mockResolvedValue(OFFICIAL_SERVICE_ON);
      renderMarket("shared");

      expect(await screen.findByText(/还没有分享过调用端点/)).toBeInTheDocument();
    });
  });

  describe("market sources", () => {
    it("lists sources in order with status, last refresh, error and official marking", async () => {
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      expect(sourceOrder()).toEqual(["ArcReel Market", "团队市场", "停用的源"]);
      const official = sourceRow("ArcReel Market");
      expect(within(official).getByText("官方")).toBeInTheDocument();
      expect(within(official).getByText("3 个条目")).toBeInTheDocument();
      expect(within(official).getByText("上次成功刷新 13分钟前")).toBeInTheDocument();

      const team = sourceRow("团队市场");
      expect(within(team).getByText("无法访问")).toBeInTheDocument();
      expect(within(team).getByText("HTTP 404")).toBeInTheDocument();
      expect(within(team).getByRole("img", { name: "无法访问" })).toBeInTheDocument();

      expect(within(sourceRow("停用的源")).getAllByText("已停用").length).toBeGreaterThan(0);
      expect(enableSwitch("停用的源")).not.toBeChecked();
      expect(screen.getByText(/该市场源由第三方维护，其中的内容未经 ArcReel 审核。/)).toBeInTheDocument();
    });

    it("keeps the official source undeletable and links its homepage from the menu", async () => {
      const open = vi.spyOn(window, "open").mockReturnValue(null);
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      await userEvent.click(screen.getByRole("button", { name: "「ArcReel Market」的更多操作" }));
      const remove = await screen.findByRole("menuitem", { name: "删除" });
      expect(remove).toHaveAttribute("aria-disabled", "true");
      expect(screen.getByText("官方市场源不能删除，可以停用")).toBeInTheDocument();
      expect(screen.getByRole("menuitem", { name: "上移" })).toHaveAttribute("aria-disabled", "true");

      await userEvent.click(screen.getByRole("menuitem", { name: "打开主页" }));
      expect(open).toHaveBeenCalledWith("https://github.com/ArcReel/arcreel-market", "_blank", "noopener,noreferrer");
    });

    it("adds a source and appends the returned row", async () => {
      const added = makeSource({ id: 4, kind: "custom", display_name: "新源", position: 3 });
      const add = vi.spyOn(API, "addMarketSource").mockResolvedValue(added);
      renderMarket("settings");
      const address = await screen.findByRole("textbox", { name: "市场源地址" });

      await userEvent.type(address, " new/market ");
      await userEvent.click(screen.getByRole("button", { name: "添加" }));

      expect(add).toHaveBeenCalledWith({ address: "new/market" });
      await waitFor(() => expect(sourceOrder()).toEqual(["ArcReel Market", "团队市场", "停用的源", "新源"]));
      expect(address).toHaveValue("");
      expect(useAppStore.getState().toast).toBeNull();
    });

    it("shows why adding a source was rejected and keeps the address", async () => {
      vi.spyOn(API, "addMarketSource").mockRejectedValue(
        new ApiRequestError("无法添加市场源：无法访问（HTTP 404）", undefined, 422),
      );
      renderMarket("settings");
      const address = await screen.findByRole("textbox", { name: "市场源地址" });

      await userEvent.type(address, "bad/market");
      await userEvent.click(screen.getByRole("button", { name: "添加" }));

      expect(await screen.findByRole("alert")).toHaveTextContent("无法添加市场源：无法访问（HTTP 404）");
      expect(address).toHaveValue("bad/market");
    });

    it("renames from the menu and toggles enablement", async () => {
      const update = vi
        .spyOn(API, "updateMarketSource")
        .mockImplementation(async (id, patch) => ({ ...TEAM, id, ...patch }));
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      await renameSource("团队市场", "同事的源");
      expect(update).toHaveBeenCalledWith(2, { display_name: "同事的源" });
      await waitFor(() => expect(sourceOrder()).toContain("同事的源"));

      await userEvent.click(enableSwitch("同事的源"));
      expect(update).toHaveBeenLastCalledWith(2, { is_enabled: false });
    });

    it("keeps concurrent rename and toggle results when their responses arrive out of order", async () => {
      const rename = createDeferred<MarketSourceInfo>();
      const toggle = createDeferred<MarketSourceInfo>();
      vi.spyOn(API, "updateMarketSource").mockImplementation((_id, patch) =>
        "display_name" in patch ? rename.promise : toggle.promise,
      );
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      await renameSource("团队市场", "同事的源");
      await userEvent.click(enableSwitch("同事的源"));

      toggle.resolve({ ...TEAM, is_enabled: false });
      rename.resolve({ ...TEAM, display_name: "同事的源", is_enabled: true });

      await waitFor(() => expect(enableSwitch("同事的源")).not.toBeChecked());
      expect(sourceOrder()).toContain("同事的源");
    });

    it("sends repeated toggles of one source in order and keeps the last intent", async () => {
      const off = createDeferred<MarketSourceInfo>();
      const on = createDeferred<MarketSourceInfo>();
      const update = vi.spyOn(API, "updateMarketSource").mockReturnValueOnce(off.promise).mockReturnValueOnce(on.promise);
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      await userEvent.click(enableSwitch("团队市场"));
      await userEvent.click(enableSwitch("团队市场"));
      expect(update).toHaveBeenCalledTimes(1);
      expect(enableSwitch("团队市场")).toBeChecked();

      on.resolve({ ...TEAM, is_enabled: true });
      off.resolve({ ...TEAM, is_enabled: false });

      await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
      expect(update).toHaveBeenLastCalledWith(2, { is_enabled: true });
      await waitFor(() => expect(enableSwitch("团队市场")).toBeChecked());
    });

    it("keeps the latest source toggle when switching away and back before responses arrive", async () => {
      const off = createDeferred<MarketSourceInfo>();
      const on = createDeferred<MarketSourceInfo>();
      vi.spyOn(API, "updateMarketSource").mockReturnValueOnce(off.promise).mockReturnValueOnce(on.promise);
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });
      await userEvent.click(enableSwitch("团队市场"));
      await userEvent.click(screen.getByRole("tab", { name: "浏览" }));
      await userEvent.click(screen.getByRole("tab", { name: "设置" }));
      await userEvent.click(enableSwitch("团队市场"));
      await act(async () => {
        on.resolve({ ...TEAM, is_enabled: true });
        off.resolve({ ...TEAM, is_enabled: false });
      });
      expect(enableSwitch("团队市场")).toBeChecked();
    });

    it("restores the last confirmed value when every queued toggle fails", async () => {
      const off = createDeferred<MarketSourceInfo>();
      const on = createDeferred<MarketSourceInfo>();
      vi.spyOn(API, "updateMarketSource").mockReturnValueOnce(off.promise).mockReturnValueOnce(on.promise);
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      await userEvent.click(enableSwitch("团队市场"));
      await userEvent.click(enableSwitch("团队市场"));
      off.reject(new Error("offline"));
      await waitFor(() => expect(API.updateMarketSource).toHaveBeenCalledTimes(2));
      on.reject(new Error("offline"));

      await waitFor(() => expect(useAppStore.getState().toast?.text).toContain("offline"));
      expect(enableSwitch("团队市场")).toBeChecked();
    });

    it("rolls back only the failed field when a concurrent update fails", async () => {
      const rename = createDeferred<MarketSourceInfo>();
      const toggle = createDeferred<MarketSourceInfo>();
      vi.spyOn(API, "updateMarketSource").mockImplementation((_id, patch) =>
        "display_name" in patch ? rename.promise : toggle.promise,
      );
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      await renameSource("团队市场", "同事的源");
      await userEvent.click(enableSwitch("同事的源"));

      toggle.resolve({ ...TEAM, is_enabled: false });
      rename.reject(new Error("rename failed"));

      await waitFor(() => expect(sourceOrder()).toContain("团队市场"));
      expect(enableSwitch("团队市场")).not.toBeChecked();
    });

    it("does not save a blank or unchanged display name", async () => {
      const update = vi.spyOn(API, "updateMarketSource");
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      await chooseSourceAction("团队市场", "重命名");
      const dialog = await screen.findByRole("dialog", { name: "重命名市场源" });
      await userEvent.clear(within(dialog).getByRole("textbox", { name: "显示名称" }));
      expect(within(dialog).getByRole("button", { name: "保存" })).toBeDisabled();
      await userEvent.type(within(dialog).getByRole("textbox", { name: "显示名称" }), "团队市场{Enter}");

      await waitFor(() => expect(dialog).not.toBeInTheDocument());
      expect(update).not.toHaveBeenCalled();
    });

    it("deletes a custom source only after confirming", async () => {
      const remove = vi.spyOn(API, "deleteMarketSource").mockResolvedValue(undefined);
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      await chooseSourceAction("团队市场", "删除");
      let confirm = await screen.findByRole("alertdialog", { name: "删除市场源「团队市场」？" });
      await userEvent.click(within(confirm).getByRole("button", { name: "取消" }));
      expect(remove).not.toHaveBeenCalled();

      await chooseSourceAction("团队市场", "删除");
      confirm = await screen.findByRole("alertdialog", { name: "删除市场源「团队市场」？" });
      await userEvent.click(within(confirm).getByRole("button", { name: "删除" }));

      expect(remove).toHaveBeenCalledWith(2);
      await waitFor(() => expect(sourceOrder()).toEqual(["ArcReel Market", "停用的源"]));
    });

    it("keeps the source and explains why deleting failed", async () => {
      vi.spyOn(API, "deleteMarketSource").mockRejectedValue(new Error("busy"));
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      await chooseSourceAction("团队市场", "删除");
      const confirm = await screen.findByRole("alertdialog");
      await userEvent.click(within(confirm).getByRole("button", { name: "删除" }));

      expect(await within(confirm).findByRole("alert")).toHaveTextContent("busy");
      await userEvent.click(within(confirm).getByRole("button", { name: "取消" }));
      await waitFor(() => expect(confirm).not.toBeInTheDocument());
      expect(sourceOrder()).toContain("团队市场");

      // 再次打开同一个源时不残留上一次的错误。
      await chooseSourceAction("团队市场", "删除");
      expect(within(await screen.findByRole("alertdialog")).queryByRole("alert")).not.toBeInTheDocument();
    });

    it("starts the rename dialog from the current name again after cancelling an edit", async () => {
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      await chooseSourceAction("团队市场", "重命名");
      const dialog = await screen.findByRole("dialog", { name: "重命名市场源" });
      await userEvent.type(within(dialog).getByRole("textbox", { name: "显示名称" }), "草稿");
      await userEvent.click(within(dialog).getByRole("button", { name: "取消" }));
      await waitFor(() => expect(dialog).not.toBeInTheDocument());

      await chooseSourceAction("团队市场", "重命名");
      const reopened = await screen.findByRole("dialog", { name: "重命名市场源" });
      expect(within(reopened).getByRole("textbox", { name: "显示名称" })).toHaveValue("团队市场");
    });

    it("refreshes one source and keeps a rename made while the refresh is in flight", async () => {
      const refresh = createDeferred<MarketSourceInfo>();
      vi.spyOn(API, "refreshMarketSource").mockReturnValue(refresh.promise);
      vi.spyOn(API, "updateMarketSource").mockImplementation(async (id, patch) => ({ ...TEAM, id, ...patch }));
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      await chooseSourceAction("团队市场", "刷新");
      expect(API.refreshMarketSource).toHaveBeenCalledWith(2);
      expect(within(sourceRow("团队市场")).getByRole("img", { name: "刷新中" })).toBeInTheDocument();
      await renameSource("团队市场", "同事的源");

      refresh.resolve({ ...TEAM, status: "invalid_index", last_error: "bad slug" });

      expect(await within(sourceRow("同事的源")).findByText("bad slug")).toBeInTheDocument();
      expect(within(sourceRow("同事的源")).getByText("索引无效")).toBeInTheDocument();
    });

    it("refreshes all enabled sources without a success toast", async () => {
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });
      vi.mocked(API.refreshMarketSources).mockResolvedValue({
        sources: [{ ...OFFICIAL }, { ...TEAM, status: "ok", last_error: null }],
      });

      await userEvent.click(screen.getByRole("button", { name: "全部刷新" }));

      expect(API.refreshMarketSources).toHaveBeenLastCalledWith();
      expect(await within(sourceRow("团队市场")).findByText("2 个条目")).toBeInTheDocument();
      expect(useAppStore.getState().toast).toBeNull();
    });

    it("moves a source up from its menu and saves the new order immediately", async () => {
      const reorder = vi.spyOn(API, "reorderMarketSources").mockResolvedValue({ sources: [TEAM, OFFICIAL, DISABLED] });
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      await chooseSourceAction("团队市场", "上移");

      expect(reorder).toHaveBeenCalledWith([2, 1, 3]);
      await waitFor(() => expect(sourceOrder()).toEqual(["团队市场", "ArcReel Market", "停用的源"]));
    });

    it("restores the order when saving it fails", async () => {
      vi.spyOn(API, "reorderMarketSources").mockRejectedValue(new Error("boom"));
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      await chooseSourceAction("ArcReel Market", "下移");

      expect(API.reorderMarketSources).toHaveBeenCalledWith([2, 1, 3]);
      await waitFor(() => expect(useAppStore.getState().toast?.text).toContain("boom"));
      expect(sourceOrder()).toEqual(["ArcReel Market", "团队市场", "停用的源"]);
    });

    it("keeps the latest order when an earlier reorder request fails after it", async () => {
      const first = createDeferred<{ sources: MarketSourceInfo[] }>();
      const second = createDeferred<{ sources: MarketSourceInfo[] }>();
      vi.spyOn(API, "reorderMarketSources").mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      await chooseSourceAction("团队市场", "上移");
      await waitFor(() => expect(sourceOrder()).toEqual(["团队市场", "ArcReel Market", "停用的源"]));
      await chooseSourceAction("停用的源", "上移");
      await waitFor(() => expect(sourceOrder()).toEqual(["团队市场", "停用的源", "ArcReel Market"]));

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
      expect(sourceOrder()).toEqual(["团队市场", "停用的源", "ArcReel Market"]);
    });

    it("keeps an enablement change made while saving a new order", async () => {
      const reorder = createDeferred<{ sources: MarketSourceInfo[] }>();
      vi.spyOn(API, "reorderMarketSources").mockReturnValue(reorder.promise);
      vi.spyOn(API, "updateMarketSource").mockImplementation(async (id, patch) => ({ ...TEAM, id, ...patch }));
      renderMarket("settings");
      await screen.findByRole("region", { name: "市场源" });

      await chooseSourceAction("团队市场", "上移");
      await userEvent.click(enableSwitch("团队市场"));
      await waitFor(() => expect(enableSwitch("团队市场")).not.toBeChecked());

      reorder.resolve({ sources: [{ ...TEAM, position: 0 }, { ...OFFICIAL, position: 1 }, DISABLED] });

      await waitFor(() => expect(sourceOrder()).toEqual(["团队市场", "ArcReel Market", "停用的源"]));
      expect(enableSwitch("团队市场")).not.toBeChecked();
    });
  });

  describe("GitHub access proxy", () => {
    it("saves the proxy prefix through the save bar", async () => {
      const patch = vi.spyOn(API, "updateSystemConfig").mockResolvedValue({
        settings: { market_github_proxy_prefix: "https://proxy.example.com/" },
      } as never);
      renderMarket("settings");

      const input = await screen.findByRole("textbox", { name: "代理前缀" });
      expect(input).toHaveValue("https://old.example.com/");
      await userEvent.clear(input);
      await userEvent.type(input, "https://proxy.example.com/");
      await userEvent.click(screen.getByRole("button", { name: "保存" }));

      await waitFor(() =>
        expect(patch).toHaveBeenCalledWith({ market_github_proxy_prefix: "https://proxy.example.com/" }),
      );
      await waitFor(() => expect(screen.getByRole("button", { name: "保存" })).toBeDisabled());
      expect(useAppStore.getState().toast).toBeNull();
    });
  });

  describe("official service", () => {
    const AGGREGATES = [
      { source_id: 1, slug: "alpha", installs: 1200, rating_count: 5, rating_average: 4.33 },
      { source_id: 1, slug: "zeta", installs: 0, rating_count: 2, rating_average: null },
    ];

    it("shows no official service elements while browsing with the service off and queries nothing", async () => {
      renderMarket();
      await screen.findAllByRole("article");
      await waitFor(() => expect(API.getOfficialService).toHaveBeenCalled());
      expect(API.listMarketEntryAggregates).not.toHaveBeenCalled();
      expect(API.listMarketSubmissions).not.toHaveBeenCalled();
      expect(screen.queryByText("安装量")).not.toBeInTheDocument();
      expect(screen.queryByRole("region", { name: /官方服务/ })).not.toBeInTheDocument();
    });

    it("turns the service back on from settings, which stay visible while it is off", async () => {
      const update = vi.spyOn(API, "updateOfficialService").mockResolvedValue(OFFICIAL_SERVICE_ON);
      renderMarket("settings");

      const section = await screen.findByRole("region", { name: "官方服务" });
      const toggle = within(section).getByRole("switch", { name: "使用官方服务" });
      expect(toggle).not.toBeChecked();
      await userEvent.click(toggle);

      expect(update).toHaveBeenCalledWith({ enabled: true });
      await waitFor(() => expect(toggle).toBeChecked());
      await waitFor(() => expect(API.listMarketSubmissions).toHaveBeenCalled());
      expect(useAppStore.getState().toast).toBeNull();
    });

    it("keeps the official service block in settings with a retry when its status fails to load", async () => {
      vi.mocked(API.getOfficialService)
        .mockRejectedValueOnce(new Error("网络中断"))
        .mockResolvedValue(OFFICIAL_SERVICE_OFF);
      renderMarket("settings");

      const failed = await screen.findByRole("region", { name: "官方服务" });
      expect(within(failed).getByRole("alert")).toHaveTextContent("网络中断");
      await userEvent.click(within(failed).getByRole("button", { name: "重试" }));

      expect(await screen.findByRole("switch", { name: "使用官方服务" })).not.toBeChecked();
      expect(within(screen.getByRole("region", { name: "官方服务" })).queryByRole("alert")).not.toBeInTheDocument();
    });

    it("stays quiet while browsing when the official service status fails to load", async () => {
      vi.mocked(API.getOfficialService).mockRejectedValue(new Error("网络中断"));
      renderMarket();

      await screen.findAllByRole("article");
      await waitFor(() => expect(API.getOfficialService).toHaveBeenCalled());
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(useAppStore.getState().toast).toBeNull();
    });

    it("regenerates the instance ID only after confirming", async () => {
      vi.mocked(API.getOfficialService).mockResolvedValue(OFFICIAL_SERVICE_ON);
      const reset = vi
        .spyOn(API, "resetOfficialInstanceId")
        .mockResolvedValue({ ...OFFICIAL_SERVICE_ON, instance_id: "inst-456" });
      renderMarket("settings");

      const section = await screen.findByRole("region", { name: "官方服务" });
      expect(within(section).getByText("inst-123")).toBeInTheDocument();
      await userEvent.click(within(section).getByRole("button", { name: "重新生成" }));
      const confirm = await screen.findByRole("alertdialog", { name: "重新生成实例标识？" });
      await userEvent.click(within(confirm).getByRole("button", { name: "重新生成" }));

      expect(reset).toHaveBeenCalled();
      expect(await within(section).findByText("inst-456")).toBeInTheDocument();
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    });

    it("explains that a build without an official service address keeps it off", async () => {
      vi.mocked(API.getOfficialService).mockResolvedValue({ ...OFFICIAL_SERVICE_OFF, available: false });
      renderMarket("settings");

      const section = await screen.findByRole("region", { name: "官方服务" });
      expect(within(section).getByText("当前版本未配置官方服务地址，官方服务处于关闭状态。")).toBeInTheDocument();
      expect(within(section).queryByRole("switch")).not.toBeInTheDocument();
    });

    it("shows install counts and ratings on official entries and explains reporting once", async () => {
      vi.mocked(API.getOfficialService).mockResolvedValue(OFFICIAL_SERVICE_FIRST_VISIT);
      vi.mocked(API.listMarketEntryAggregates).mockResolvedValue({ items: AGGREGATES });
      const update = vi
        .spyOn(API, "updateOfficialService")
        .mockResolvedValue({ ...OFFICIAL_SERVICE_FIRST_VISIT, notice_seen: true });
      renderMarket();

      const alpha = await screen.findByRole("article", { name: "Alpha Video" });
      await waitFor(() => expect(within(alpha).getByText("安装量")).toBeInTheDocument());
      expect(alpha).toHaveTextContent("1,200");
      expect(within(alpha).getByText("平均 4.3 星，5 人评分")).toBeInTheDocument();
      const zeta = screen.getByRole("article", { name: "Zeta Gateway" });
      expect(within(zeta).getByText("2 人评分，人数足够后显示平均分")).toBeInTheDocument();
      expect(within(screen.getByRole("article", { name: "Alpha 团队版" })).queryByText("安装量")).not.toBeInTheDocument();

      const notice = screen.getByRole("region", { name: "安装量与评分来自 ArcReel 官方服务" });
      expect(within(notice).getByRole("link", { name: "设置" })).toHaveAttribute(
        "href",
        "/app/settings?section=market&tab=settings",
      );
      await userEvent.click(within(notice).getByRole("button", { name: "知道了" }));
      expect(update).toHaveBeenCalledWith({ notice_seen: true });
      await waitFor(() => expect(notice).not.toBeInTheDocument());
      expect(within(alpha).getByText("安装量")).toBeInTheDocument();
    });

    it("turns the service off from the notice and drops every official element from browsing", async () => {
      vi.mocked(API.getOfficialService).mockResolvedValue(OFFICIAL_SERVICE_FIRST_VISIT);
      vi.mocked(API.listMarketEntryAggregates).mockResolvedValue({ items: AGGREGATES });
      const update = vi.spyOn(API, "updateOfficialService").mockResolvedValue(OFFICIAL_SERVICE_OFF);
      renderMarket();

      const notice = await screen.findByRole("region", { name: "安装量与评分来自 ArcReel 官方服务" });
      const alpha = await screen.findByRole("article", { name: "Alpha Video" });
      await waitFor(() => expect(within(alpha).getByText("安装量")).toBeInTheDocument());
      await userEvent.click(within(notice).getByRole("button", { name: "关闭官方服务" }));
      expect(update).toHaveBeenCalledWith({ enabled: false, notice_seen: true });
      await waitFor(() => expect(notice).not.toBeInTheDocument());
      expect(screen.queryByText("安装量")).not.toBeInTheDocument();
    });

    it("keeps browsing without numbers when aggregates cannot be fetched", async () => {
      vi.mocked(API.getOfficialService).mockResolvedValue(OFFICIAL_SERVICE_ON);
      vi.mocked(API.listMarketEntryAggregates).mockRejectedValue(
        new ApiRequestError("暂时无法连接官方服务", undefined, 502),
      );
      renderMarket();
      await screen.findAllByRole("article");
      await waitFor(() => expect(API.listMarketEntryAggregates).toHaveBeenCalled());
      expect(screen.queryByText("安装量")).not.toBeInTheDocument();
      expect(useAppStore.getState().toast).toBeNull();
    });
  });
});
