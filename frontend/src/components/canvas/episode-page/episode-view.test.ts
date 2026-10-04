import { describe, expect, it } from "vitest";

import {
  defaultEpisodeView,
  episodeViewTabs,
  resolveEpisodeView,
  staysInEpisodeView,
  withEpisodeView,
  type EpisodeViewFacts,
} from "./episode-view";

const SCRIPTED: EpisodeViewFacts = {
  isAd: false,
  route: "storyboard",
  grid: false,
  hasScript: true,
  hasDraft: true,
  sourceReview: false,
  demo: false,
};

describe("episodeViewTabs", () => {
  it("lists plan, grid, board and edit for a scripted grid episode", () => {
    expect(episodeViewTabs({ ...SCRIPTED, grid: true })).toEqual([
      { view: "plan", disabled: false },
      { view: "grid", disabled: false },
      { view: "board", disabled: false },
      { view: "edit", disabled: false },
    ]);
  });

  it("keeps the board closed while a storyboard episode only has its script plan", () => {
    const tabs = episodeViewTabs({ ...SCRIPTED, hasScript: false });
    expect(tabs).toEqual([
      { view: "plan", disabled: false },
      { view: "board", disabled: true },
    ]);
  });

  it("opens the unit list during the draft stage on the reference route", () => {
    const tabs = episodeViewTabs({ ...SCRIPTED, route: "reference_video", hasScript: false });
    expect(tabs.find((tab) => tab.view === "board")).toEqual({ view: "board", disabled: false });
  });

  it("disables everything but the plan while the episode source is under review", () => {
    const facts = { ...SCRIPTED, grid: true, hasScript: false, hasDraft: false, sourceReview: true };
    expect(episodeViewTabs(facts)).toEqual([
      { view: "plan", disabled: false },
      { view: "grid", disabled: true },
      { view: "board", disabled: true },
    ]);
  });

  it("gives ad projects no plan view and demo projects no edit view", () => {
    expect(episodeViewTabs({ ...SCRIPTED, isAd: true }).map((tab) => tab.view)).toEqual(["board", "edit"]);
    expect(episodeViewTabs({ ...SCRIPTED, demo: true, hasDraft: false }).map((tab) => tab.view)).toEqual(["board"]);
  });
});

describe("resolveEpisodeView", () => {
  it("lands on the board once the script exists, otherwise on the plan", () => {
    expect(defaultEpisodeView(SCRIPTED)).toBe("board");
    expect(defaultEpisodeView({ ...SCRIPTED, hasScript: false })).toBe("plan");
    expect(defaultEpisodeView({ ...SCRIPTED, hasScript: false, hasDraft: false })).toBe("board");
  });

  it("honours an available view and falls back from unknown or disabled ones", () => {
    expect(resolveEpisodeView("edit", SCRIPTED)).toBe("edit");
    expect(resolveEpisodeView("grid", SCRIPTED)).toBe("board");
    expect(resolveEpisodeView("nonsense", SCRIPTED)).toBe("board");
    expect(resolveEpisodeView(null, SCRIPTED)).toBe("board");
    expect(resolveEpisodeView("board", { ...SCRIPTED, hasScript: false })).toBe("plan");
  });
});

describe("withEpisodeView", () => {
  it("writes only non-default views, keeps other params and leaves the input untouched", () => {
    const params = new URLSearchParams("unit=SEG-3");

    expect(withEpisodeView(params, "plan", SCRIPTED).toString()).toBe("unit=SEG-3&view=plan");
    expect(withEpisodeView(new URLSearchParams("view=plan&unit=SEG-3"), "board", SCRIPTED).toString()).toBe(
      "unit=SEG-3",
    );
    expect(params.toString()).toBe("unit=SEG-3");
  });
});

describe("staysInEpisodeView", () => {
  it("lets navigation through only while the resolved view stays the same", () => {
    expect(staysInEpisodeView("/episodes/1", "/episodes/1?view=board", SCRIPTED)).toBe(true);
    expect(staysInEpisodeView("/episodes/1?view=board&unit=2", "/episodes/1", SCRIPTED)).toBe(true);
    expect(staysInEpisodeView("/episodes/1?view=plan", "/episodes/1", SCRIPTED)).toBe(false);
    expect(staysInEpisodeView("?view=grid", "/episodes/1", { ...SCRIPTED, grid: true })).toBe(false);
    expect(staysInEpisodeView("/episodes/1?view=edit", "/episodes/1", SCRIPTED)).toBe(false);
    expect(staysInEpisodeView("/episodes/2", "/episodes/1", SCRIPTED)).toBe(false);
  });

  it("resolves a missing or unavailable view to the default view of the episode", () => {
    // 参考生视频在中间稿阶段缺省停在脚本规划：去掉 view 就是离开视频单元
    const referenceDraft: EpisodeViewFacts = { ...SCRIPTED, route: "reference_video", hasScript: false };
    expect(staysInEpisodeView("/episodes/1", "/episodes/1?view=board", referenceDraft)).toBe(false);
    expect(staysInEpisodeView("/episodes/1?view=plan", "/episodes/1", referenceDraft)).toBe(true);
    // 没开宫格时 view=grid 不可选，按缺省视图（分镜）显示
    expect(staysInEpisodeView("/episodes/1?view=grid", "/episodes/1", SCRIPTED)).toBe(true);
  });
});
