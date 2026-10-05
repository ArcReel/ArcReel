import { describe, expect, it } from "vitest";

import type { EpisodeMeta, ProjectData } from "@/types";
import type { EpisodeScript } from "@/types/script";

import { showsEpisodeSource } from "./EpisodePage";

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
