import { describe, expect, it } from "vitest";

import { findEpisodeItem, itemExcerpt } from "./episode-item";

const narration = {
  episode: 3,
  title: "城门",
  content_mode: "narration",
  segments: [
    { segment_id: "E3S01", novel_text: "第一段", generated_assets: { storyboard_image: "storyboards/a.png" } },
    { segment_id: "E3S07", novel_text: "风从城门外灌进来，吹灭了所有的灯笼和火把" },
    { segment_id: "E3S04", novel_text: "第三段" },
  ],
};

const reference = {
  episode: 9,
  title: "",
  content_mode: "drama",
  video_units: [
    { unit_id: "E9U2", text: "@[张三] 推门进来", generated_assets: { video_thumbnail: "thumbs/u2.jpg", storyboard_image: null } },
    { unit_id: "E9U5", text: "第二单元" },
  ],
};

const scripts = { "episode_3.json": narration, "episode_9.json": reference };

describe("findEpisodeItem", () => {
  it("reports the item's current 1-based position in its episode, not its ID number", () => {
    const item = findEpisodeItem(scripts, "E3S07");
    expect(item).toMatchObject({ episodeId: 3, kind: "shot", position: 2, total: 3 });
  });

  it("follows the array order after an insertion moves the item", () => {
    const reordered = {
      "episode_3.json": {
        ...narration,
        segments: [{ segment_id: "E3S09", novel_text: "新插入" }, ...narration.segments],
      },
    };
    expect(findEpisodeItem(reordered, "E3S07")).toMatchObject({ position: 3, total: 4 });
  });

  it("treats reference-video units as video units and reads their thumbnail", () => {
    expect(findEpisodeItem(scripts, "E9U5")).toMatchObject({ kind: "unit", position: 2, total: 2 });
    expect(findEpisodeItem(scripts, "E9U2")?.thumbnail).toBe("thumbs/u2.jpg");
  });

  it("falls back to the storyboard image when there is no video thumbnail", () => {
    expect(findEpisodeItem(scripts, "E3S01")?.thumbnail).toBe("storyboards/a.png");
  });

  it("returns null for an item no longer in the script", () => {
    expect(findEpisodeItem(scripts, "E3S02")).toBeNull();
    expect(findEpisodeItem(scripts, "E5S01")).toBeNull();
    expect(findEpisodeItem({}, "E3S01")).toBeNull();
  });
});

describe("itemExcerpt", () => {
  it("keeps the first characters of the body and marks the cut", () => {
    expect(findEpisodeItem(scripts, "E3S07")?.excerpt).toBe("风从城门外灌进来，吹灭了所有的灯…");
    expect(findEpisodeItem(scripts, "E3S01")?.excerpt).toBe("第一段");
  });

  it("drops the mention brackets of reference-video bodies", () => {
    expect(findEpisodeItem(scripts, "E9U2")?.excerpt).toBe("@张三 推门进来");
  });

  it("uses the visual description for drama scenes and the voiceover for ad shots", () => {
    expect(itemExcerpt({ scene_id: "E1S01", scene_description: "雨夜，他独自走过街头", image_prompt: { scene: "x" } })).toBe(
      "雨夜，他独自走过街头",
    );
    expect(itemExcerpt({ scene_id: "E1S01", image_prompt: { scene: "雨夜街头" } })).toBe("雨夜街头");
    expect(itemExcerpt({ scene_id: "E1S01", image_prompt: "雨夜街头，霓虹" })).toBe("雨夜街头，霓虹");
    expect(itemExcerpt({ shot_id: "E1S01", voiceover_text: "限时五折", image_prompt: { scene: "货架" } })).toBe(
      "限时五折",
    );
    expect(itemExcerpt({ shot_id: "E1S01", voiceover_text: " ", image_prompt: { scene: "货架" } })).toBe("货架");
  });

  it("falls back to the source text for scenes whose visuals are not written yet", () => {
    expect(itemExcerpt({ scene_id: "E1S01", image_prompt: null, source_text: "原文一句" })).toBe("原文一句");
  });
});
