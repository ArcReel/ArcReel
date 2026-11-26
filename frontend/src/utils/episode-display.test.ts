import { describe, expect, it } from "vitest";

import i18n from "@/i18n";

import { episodeItemAgentRef, episodeItemLabel, itemIdsAsPositionsInText } from "./episode-display";

const t = i18n.t.bind(i18n);

const episodes = [
  { episode: 3, title: "雨夜" },
  { episode: 9, title: "" },
];

const narration = {
  episode: 3,
  segments: [
    { segment_id: "E3S01", novel_text: "第一段" },
    { segment_id: "E3S07", novel_text: "第二段" },
  ],
};

const reference = {
  episode: 9,
  video_units: [
    { unit_id: "E9U2", text: "第一单元" },
    { unit_id: "E9U5", text: "第二单元" },
  ],
};

const scripts = { "episode_3.json": narration, "episode_9.json": reference };

describe("episodeItemLabel", () => {
  it("names a present item by its position in the episode, not its ID number", () => {
    expect(episodeItemLabel("E3S07", scripts, episodes, t)).toBe("雨夜 · 2");
    expect(episodeItemLabel("E9U5", scripts, episodes, t)).toBe("第 2 集 · 2");
  });

  it("says the item is deleted when it is no longer in the script", () => {
    expect(episodeItemLabel("E3S04", scripts, episodes, t)).toBe("雨夜 · 已删除");
  });

  it("keeps the in-episode ID for grids", () => {
    expect(episodeItemLabel("E3G01", scripts, episodes, t)).toBe("雨夜 · G01");
  });

  it("moves the position when an item is inserted before it", () => {
    const inserted = {
      "episode_3.json": {
        ...narration,
        segments: [{ segment_id: "E3S09", novel_text: "新插入" }, ...narration.segments],
      },
    };
    expect(episodeItemLabel("E3S07", inserted, episodes, t)).toBe("雨夜 · 3");
  });
});

describe("episodeItemAgentRef", () => {
  it("gives the agent both the position and the full item ID", () => {
    expect(episodeItemAgentRef("E3S07", scripts, t)).toBe("分镜 2（ID E3S07）");
    expect(episodeItemAgentRef("E9U2", scripts, t, episodes)).toBe("第 2 集 · 视频单元 1（ID E9U2）");
  });
});

describe("itemIdsAsPositionsInText", () => {
  it("replaces shot and unit IDs with positions and strips the prefix from other items", () => {
    expect(itemIdsAsPositionsInText("将丢失 E3S07、E9U2、E3S04 与 E3G01", scripts, t)).toBe(
      "将丢失 分镜 2、视频单元 1、已删除 与 G01",
    );
  });
});
