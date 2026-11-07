import { describe, expect, it } from "vitest";

import type { EpisodesView, EpisodesViewSegment, ReplanCandidateEpisode, ReplanSummary } from "@/types";

import { lanesContinue, replanCompare } from "./replan-compare-model";

function segment(overrides: Partial<EpisodesViewSegment>): EpisodesViewSegment {
  return {
    kind: "episode",
    start: 0,
    end: 0,
    text: "",
    episode: null,
    gap: false,
    units: 0,
    continued: false,
    continues: false,
    ...overrides,
  };
}

// One 40-character file: episode 1 [0, 10), 2 [10, 20), 3 [20, 30), 4 [30, 40); episode 3 starts with a newline.
const view: EpisodesView = {
  unit: "chars",
  units: 40,
  cut_units: 40,
  episodes: [],
  unregistered: [],
  replan: null,
  external_changes: [],
  files: [
    {
      source_file: "source/a.txt",
      name: "a.txt",
      original_filename: null,
      missing: false,
      changed_outside: false,
      length: 40,
      units: 40,
      cut_units: 40,
      source_kind: null,
      segments: [
        segment({ episode: 1, start: 0, end: 10, text: "一".repeat(10) }),
        segment({ episode: 2, start: 10, end: 20, text: "二".repeat(10) }),
        segment({ episode: 3, start: 20, end: 30, text: `\n${"三".repeat(9)}` }),
        segment({ episode: 4, start: 30, end: 40, text: "四".repeat(10) }),
      ],
    },
  ],
};

function sourceFile(name: string, segments: EpisodesViewSegment[]) {
  return { ...view.files[0], source_file: `source/${name}`, name, length: 20, segments };
}

// a.txt: episode 1 [0, 10), episode 2 [10, 20) continues into b.txt; b.txt: episode 2 [0, 10), episode 3 [10, 20)
const crossing: EpisodesView = {
  ...view,
  files: [
    sourceFile("a.txt", [
      segment({ episode: 1, start: 0, end: 10, text: "一".repeat(10) }),
      segment({ episode: 2, start: 10, end: 20, text: "二".repeat(10), continues: true }),
    ]),
    sourceFile("b.txt", [
      segment({ episode: 2, start: 0, end: 10, text: "二".repeat(10), continued: true }),
      segment({ episode: 3, start: 10, end: 20, text: "三".repeat(10) }),
    ]),
  ],
};

function draft(start: number, end: number): ReplanCandidateEpisode {
  return {
    title: "",
    hook: "",
    source_file: "source/a.txt",
    start,
    end,
    units: end - start,
    first_sentence: "",
    last_sentence: "",
    same_as: null,
    overlaps: [],
  };
}

function summary(episodes: ReplanCandidateEpisode[], overrides: Partial<ReplanSummary> = {}): ReplanSummary {
  return {
    id: "c1",
    episode: 2,
    instructions: null,
    complete: false,
    interrupted: null,
    stale: null,
    start: { source_file: "source/a.txt", offset: 10 },
    end: { source_file: "source/a.txt", offset: episodes.at(-1)?.end ?? 10 },
    old_count: 3,
    new_count: episodes.length,
    units: 0,
    average_units: null,
    retired: [],
    removed: [],
    needs_review: [],
    uncovered: [],
    moved: [],
    episodes,
    ...overrides,
  };
}

describe("新旧分法的对照", () => {
  it("生成中：起点之前没有右侧色条，已生成的部分按新集分段，之后等待规划", () => {
    const compare = replanCompare(view, summary([draft(10, 25)]))!;

    expect(compare.lanes(0, 0, 10)).toEqual([]);
    expect(compare.lanes(0, 20, 30)).toEqual([
      { from: 20, to: 25, lane: { kind: "new", index: 0 } },
      { from: 25, to: 30, lane: { kind: "pending" } },
    ]);
    expect([...compare.waiting]).toEqual([4]);
  });

  it("只标出分界不同处，落在空白里的分界与紧随其后的分界视为同一处", () => {
    // Candidate splits at 21 after newline, same boundary as episode 3 start 20; 25 is candidate-only, 30 existing-only.
    const compare = replanCompare(view, summary([draft(10, 21), draft(21, 25), draft(25, 40)], { complete: true }))!;

    expect(compare.diffs(0)).toEqual([25, 30]);
    expect(compare.waiting.size).toBe(0);
  });

  it("生成中只比到方案的结尾，起点本身不算分界不同", () => {
    const compare = replanCompare(view, summary([draft(10, 25)]))!;

    // Episode 3 starts with a newline; mark its first character after the newline.
    expect(compare.diffs(0)).toEqual([21]);
  });

  it("相邻两行属于同一集时色条连成一条", () => {
    const compare = replanCompare(view, summary([draft(10, 25)]))!;

    expect(lanesContinue(compare.lanes(0, 10, 15), compare.lanes(0, 16, 20))).toBe(true);
    expect(lanesContinue(compare.lanes(0, 20, 25), compare.lanes(0, 26, 30))).toBe(false);
    expect(lanesContinue(compare.lanes(0, 0, 5), compare.lanes(0, 6, 9))).toBe(false);
  });

  it("跨文件的一集在文件交界处不算分界，方案在文件结尾的分界就是下一个文件的开头", () => {
    // Candidate: one episode a.txt [10, 20), another using all b.txt.
    const drafts = [draft(10, 20), { ...draft(0, 20), source_file: "source/b.txt" }];
    const compare = replanCompare(
      crossing,
      summary(drafts, { complete: true, end: { source_file: "source/b.txt", offset: 20 } }),
    )!;

    expect(compare.diffs(0)).toEqual([]);
    expect(compare.diffs(1)).toEqual([0, 10]);
  });

  it("生成中的方案只到第一个文件时，跨文件的集按起点判断是否等待规划", () => {
    const compare = replanCompare(crossing, summary([draft(10, 20)], { end: { source_file: "source/a.txt", offset: 20 } }))!;

    expect([...compare.waiting]).toEqual([3]);
    expect(compare.lanes(1, 0, 10)).toEqual([{ from: 0, to: 10, lane: { kind: "pending" } }]);
  });

  it("过时的方案不做对照", () => {
    expect(replanCompare(view, summary([draft(10, 25)], { stale: "ledger_changed" }))).toBeNull();
    expect(replanCompare(view, null)).toBeNull();
  });
});
