import { describe, expect, it } from "vitest";

import type { EpisodeMeta, ProjectData } from "@/types";
import type { EpisodeScript } from "@/types/script";

import { canViewEpisodeSource, showsEpisodeSource } from "./episode-view";

function project(episode: Partial<EpisodeMeta> | null, contentMode = "narration"): ProjectData {
  return {
    content_mode: contentMode,
    episodes: episode ? [{ episode: 3, title: "雨夜", script_file: "", ...episode }] : [],
  } as unknown as ProjectData;
}

const SCRIPT = { episode: 3 } as unknown as EpisodeScript;

describe("showsEpisodeSource", () => {
  it("只有集原文、没有剧本与中间稿的集呈现集原文", () => {
    expect(showsEpisodeSource(project({}), 3, null, false)).toBe(true);
    expect(showsEpisodeSource(project({ script_status: "none" }), 3, null, false)).toBe(true);
  });

  it("项目里没有这一集，或项目数据还没加载时不呈现", () => {
    expect(showsEpisodeSource(project(null), 3, null, false)).toBe(false);
    expect(showsEpisodeSource(project({}), 4, null, false)).toBe(false);
    expect(showsEpisodeSource(null, 3, null, false)).toBe(false);
  });

  it("已有正式剧本时不呈现", () => {
    expect(showsEpisodeSource(project({}), 3, SCRIPT, false)).toBe(false);
  });

  it.each(["segmented", "generated"] as const)("已有中间稿（%s）时不呈现", (status) => {
    expect(showsEpisodeSource(project({ script_status: status }), 3, null, false)).toBe(false);
  });

  it("广告/短片与演示项目没有源文可切，不呈现", () => {
    expect(showsEpisodeSource(project({}, "ad"), 3, null, false)).toBe(false);
    expect(showsEpisodeSource(project({}), 3, null, true)).toBe(false);
  });
});

describe("canViewEpisodeSource", () => {
  it.each(["own", "whole_source"] as const)("已有正式剧本且账本记为有原文（%s）时提供入口", (origin) => {
    expect(canViewEpisodeSource(project({ source_origin: origin }), 3, SCRIPT, false)).toBe(true);
  });

  it("账本没写来源时按原文范围推断", () => {
    expect(canViewEpisodeSource(project({ source_range: { source_file: "source/novel.txt" } }), 3, SCRIPT, false)).toBe(true);
    expect(canViewEpisodeSource(project({}), 3, SCRIPT, false)).toBe(false);
  });

  it("账本记为无原文的集不提供入口", () => {
    expect(canViewEpisodeSource(project({ source_origin: "none" }), 3, SCRIPT, false)).toBe(false);
  });

  it("还没有正式剧本时不提供入口，原文由脚本规划视图呈现", () => {
    expect(canViewEpisodeSource(project({ source_origin: "own" }), 3, null, false)).toBe(false);
  });

  it("项目里没有这一集，或项目数据还没加载时不提供入口", () => {
    expect(canViewEpisodeSource(project({ source_origin: "own" }), 4, SCRIPT, false)).toBe(false);
    expect(canViewEpisodeSource(null, 3, SCRIPT, false)).toBe(false);
  });

  it("广告/短片与演示项目不提供入口", () => {
    expect(canViewEpisodeSource(project({ source_origin: "own" }, "ad"), 3, SCRIPT, false)).toBe(false);
    expect(canViewEpisodeSource(project({ source_origin: "own" }), 3, SCRIPT, true)).toBe(false);
  });
});
