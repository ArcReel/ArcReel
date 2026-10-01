import { describe, expect, it } from "vitest";

import type { EpisodeMeta } from "@/types";

import { episodeMoveCheck } from "./episode-order";

const files = [{ source_file: "source/a.txt" }, { source_file: "source/b.txt" }];

function cut(episode: number, sourceFile: string, start: number): EpisodeMeta {
  return {
    episode,
    title: "",
    script_file: `scripts/episode_${episode}.json`,
    source_origin: "whole_source",
    source_range: { source_file: sourceFile, start, end: start + 10 },
  };
}

function own(episode: number): EpisodeMeta {
  return { episode, title: "", script_file: `scripts/episode_${episode}.json`, source_origin: "own" };
}

// 播出顺序：切出集 1（a.txt 开头）、自带原文的集 5、切出集 2（a.txt 后段）、切出集 3（b.txt 开头）
const episodes = [cut(1, "source/a.txt", 0), own(5), cut(2, "source/a.txt", 10), cut(3, "source/b.txt", 0)];

describe("episodeMoveCheck", () => {
  it("lets own-source and no-source episodes go anywhere, including between cut episodes", () => {
    expect(episodeMoveCheck(episodes, files, 5, null)).toBe("ok");
    expect(episodeMoveCheck(episodes, files, 5, 2)).toBe("ok");
    expect(episodeMoveCheck(episodes, files, 5, 3)).toBe("ok");
  });

  it("lets a cut episode move past episodes of other origins", () => {
    expect(episodeMoveCheck(episodes, files, 1, 5)).toBe("ok");
    expect(episodeMoveCheck(episodes, files, 2, 1)).toBe("ok");
  });

  it("refuses moves that put cut episodes out of source order, across files too", () => {
    expect(episodeMoveCheck(episodes, files, 1, 2)).toBe("locked");
    expect(episodeMoveCheck(episodes, files, 3, 1)).toBe("locked");
    expect(episodeMoveCheck(episodes, files, 2, null)).toBe("locked");
  });

  it("treats a move onto the same place as no move", () => {
    expect(episodeMoveCheck(episodes, files, 5, 1)).toBe("noop");
    expect(episodeMoveCheck(episodes, files, 1, null)).toBe("noop");
  });
});
