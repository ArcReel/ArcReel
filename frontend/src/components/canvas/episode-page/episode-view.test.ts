import { describe, expect, it } from "vitest";

import {
  defaultEpisodeView,
  episodeViewTabs,
  resolveEpisodeView,
  staysOnEpisodeCanvas,
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

describe("staysOnEpisodeCanvas", () => {
  it("lets canvas view switches through and stops edit or other pages", () => {
    expect(staysOnEpisodeCanvas("/episodes/1?view=plan", "/episodes/1")).toBe(true);
    expect(staysOnEpisodeCanvas("?view=grid", "/episodes/1?view=plan")).toBe(true);
    expect(staysOnEpisodeCanvas("/episodes/1?view=edit", "/episodes/1")).toBe(false);
    expect(staysOnEpisodeCanvas("/episodes/2", "/episodes/1")).toBe(false);
  });
});
