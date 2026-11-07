import { describe, expect, it } from "vitest";

import type { EpisodesView, EpisodesViewFile, EpisodesViewSegment } from "@/types";

import {
  adjacentBoundaries,
  cutEpisodeActions,
  pointInRun,
  rangeUnits,
  resolvePointAction,
  stepPoint,
  textRuns,
} from "./manual-split-model";

/** Source with one Han character per code point, simplifying reading-unit volume checks. */
function chars(count: number): string {
  return "字".repeat(count);
}

function segment(overrides: Partial<EpisodesViewSegment>): EpisodesViewSegment {
  return { kind: "unsplit", start: 0, end: 0, text: "", episode: null, gap: false, units: 0, continued: false, continues: false, ...overrides };
}

function file(name: string, length: number, segments: EpisodesViewSegment[]): EpisodesViewFile {
  return {
    source_file: `source/${name}`,
    name,
    original_filename: null,
    missing: false,
    changed_outside: false,
    length,
    units: 0,
    cut_units: 0,
    segments,
    source_kind: null,
  };
}

// a.txt: episode 1 [0, 10), episode 2 [10, 20), unsplit [20, 30); b.txt: unsplit [0, 8)
const view: EpisodesView = {
  unit: "chars",
  units: 0,
  cut_units: 0,
  episodes: [],
  unregistered: [],
  replan: null,
  external_changes: [],
  files: [
    file("a.txt", 30, [
      segment({ kind: "episode", episode: 1, start: 0, end: 10 }),
      segment({ kind: "episode", episode: 2, start: 10, end: 20 }),
      segment({ start: 20, end: 30 }),
    ]),
    file("b.txt", 8, [segment({ start: 0, end: 8 })]),
  ],
};

describe("落点", () => {
  it("行内光标按码位换算成文件内偏移，补充平面字符算一个字", () => {
    const text = "\n\n第一行😀尾\n第二行";
    const runs = textRuns(text, 100);

    expect(runs).toEqual([
      { start: 102, text: "第一行😀尾" },
      { start: 108, text: "第二行" },
    ]);
    // UTF-16 index before “尾” is 5 (😀 occupies two code units), code-point offset 4.
    expect(pointInRun(0, runs[0].start, runs[0].text, 5)).toEqual({ file: 0, offset: 106 });
    expect(pointInRun(1, runs[1].start, runs[1].text, 99)).toEqual({ file: 1, offset: 111 });
  });

  it("切出集内部为拆分，未切分的原文上为切分，新集从这段未切分原文的开头起", () => {
    expect(resolvePointAction(view, { file: 0, offset: 5 })).toEqual({
      kind: "split",
      file: 0,
      offset: 5,
      episode: 1,
      start: 0,
      at: 5,
      end: 10,
    });
    expect(resolvePointAction(view, { file: 0, offset: 25 })).toEqual({
      kind: "cut",
      file: 0,
      offset: 25,
      start: 20,
      end: 25,
    });
    expect(resolvePointAction(view, { file: 0, offset: 20 })).toBeNull();
    expect(resolvePointAction(view, { file: 1, offset: 0 })).toBeNull();
  });

  it("后一个文件里的切分从前一个文件里未切分的原文接起，新集跨文件", () => {
    // Global offsets: a.txt occupies [0, 30), b.txt starts at 30.
    expect(resolvePointAction(view, { file: 1, offset: 3 })).toEqual({
      kind: "cut",
      file: 1,
      offset: 3,
      start: 20,
      end: 33,
    });
  });

  it("切分不跨过源文件类型的切换处", () => {
    const kinds: EpisodesView = {
      ...view,
      files: [{ ...view.files[0], source_kind: "novel" }, { ...view.files[1], source_kind: "screenplay" }],
    };
    expect(resolvePointAction(kinds, { file: 1, offset: 3 })).toMatchObject({ kind: "cut", start: 30, end: 33 });
    expect(cutEpisodeActions(kinds, 2)).toEqual({ placed: true, merge: "none", clearAfter: false });
  });

  it("移动分界时只接受两集范围之内、原分界之外的落点", () => {
    expect(resolvePointAction(view, { file: 0, offset: 14 }, 1)).toMatchObject({
      kind: "move",
      left: 1,
      right: 2,
      boundary: 10,
      at: 14,
    });
    expect(resolvePointAction(view, { file: 0, offset: 10 }, 1)).toBeNull();
    expect(resolvePointAction(view, { file: 0, offset: 25 }, 1)).toBeNull();
    expect(resolvePointAction(view, { file: 0, offset: 14 }, 2)).toBeNull();
  });
});

describe("←/→ 微调", () => {
  it("逐字与一次 10 字移动", () => {
    expect(stepPoint(view, { file: 0, offset: 5 }, 1)).toEqual({ file: 0, offset: 6 });
    expect(stepPoint(view, { file: 0, offset: 5 }, -1)).toEqual({ file: 0, offset: 4 });
    expect(stepPoint(view, { file: 0, offset: 22 }, 10)).toEqual({ file: 1, offset: 2 });
  });

  it("跨过文件边界：后一个文件的开头换算成前一个文件的末尾", () => {
    expect(stepPoint(view, { file: 0, offset: 29 }, 1)).toEqual({ file: 0, offset: 30 });
    expect(stepPoint(view, { file: 0, offset: 30 }, 1)).toEqual({ file: 1, offset: 1 });
    expect(stepPoint(view, { file: 1, offset: 1 }, -1)).toEqual({ file: 0, offset: 30 });
    expect(stepPoint(view, { file: 1, offset: 3 }, -10)).toEqual({ file: 0, offset: 23 });
  });

  it("只在同一种操作里移动：越过原分界，到头时停在最远的可用位置", () => {
    expect(stepPoint(view, { file: 0, offset: 11 }, -1, 1)).toEqual({ file: 0, offset: 9 });
    expect(stepPoint(view, { file: 0, offset: 5 }, 10)).toEqual({ file: 0, offset: 9 });
    expect(stepPoint(view, { file: 0, offset: 21 }, -1)).toEqual({ file: 0, offset: 21 });
    expect(stepPoint(view, { file: 0, offset: 19 }, 1, 1)).toEqual({ file: 0, offset: 19 });
    expect(stepPoint(view, { file: 1, offset: 8 }, 1)).toEqual({ file: 1, offset: 8 });
  });
});

describe("跨文件的集", () => {
  // a.txt: episode 1 [0, 10), episode 2 [10, 20) continues into b.txt [0, 4); b.txt: episode 3 [4, 8)
  const crossing: EpisodesView = {
    ...view,
    files: [
      file("a.txt", 20, [
        segment({ kind: "episode", episode: 1, start: 0, end: 10, text: chars(10) }),
        segment({ kind: "episode", episode: 2, start: 10, end: 20, text: chars(10), continues: true }),
      ]),
      file("b.txt", 8, [
        segment({ kind: "episode", episode: 2, start: 0, end: 4, text: chars(4), continued: true }),
        segment({ kind: "episode", episode: 3, start: 4, end: 8, text: chars(4) }),
      ]),
    ],
  };

  it("在后一个文件里拆分：前半段从这一集在前一个文件里的起点算起", () => {
    expect(resolvePointAction(crossing, { file: 1, offset: 2 })).toEqual({
      kind: "split",
      file: 1,
      offset: 2,
      episode: 2,
      start: 10,
      at: 22,
      end: 24,
    });
    expect(rangeUnits(crossing, 10, 22)).toBe(12);
  });

  it("分界按钮放在右侧一集起点所在的文件里，可以把分界移回前一个文件", () => {
    expect(adjacentBoundaries(crossing)).toEqual([
      { left: 1, right: 2, file: 0 },
      { left: 2, right: 3, file: 1 },
    ]);
    expect(resolvePointAction(crossing, { file: 0, offset: 15 }, 2)).toMatchObject({
      kind: "move",
      file: 0,
      offset: 15,
      left: 2,
      right: 3,
      start: 10,
      boundary: 24,
      at: 15,
      end: 28,
    });
  });

  it("合并不跨过源文件类型的切换处", () => {
    const kinds: EpisodesView = {
      ...crossing,
      files: [
        { ...crossing.files[0], source_kind: "novel" },
        { ...crossing.files[1], source_kind: "novel" },
      ],
    };
    expect(cutEpisodeActions(kinds, 2).merge).toBe("ok");
    const switched: EpisodesView = {
      ...crossing,
      files: [
        { ...crossing.files[0], source_kind: "novel" },
        { ...crossing.files[1], source_kind: "screenplay" },
      ],
    };
    // Merging episodes 1/2 adds no new file crossing; episode 2 already crosses, so merging with episode 3 adds none either.
    expect(cutEpisodeActions(switched, 1).merge).toBe("ok");
    expect(resolvePointAction(switched, { file: 1, offset: 6 }, 2)).toMatchObject({ kind: "move", at: 26 });
  });
});
