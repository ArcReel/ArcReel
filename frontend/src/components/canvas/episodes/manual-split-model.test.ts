import { describe, expect, it } from "vitest";

import type { EpisodesView, EpisodesViewFile, EpisodesViewSegment } from "@/types";

import { pointInRun, resolvePointAction, stepPoint, textRuns } from "./manual-split-model";

function segment(overrides: Partial<EpisodesViewSegment>): EpisodesViewSegment {
  return { kind: "unsplit", start: 0, end: 0, text: "", episode: null, gap: false, units: 0, ...overrides };
}

function file(name: string, length: number, segments: EpisodesViewSegment[]): EpisodesViewFile {
  return {
    source_file: `source/${name}`,
    name,
    original_filename: null,
    missing: false,
    length,
    units: 0,
    cut_units: 0,
    segments,
    source_kind: null,
  };
}

// a.txt：第 1 集 [0, 10)、第 2 集 [10, 20)、未切分 [20, 30)；b.txt：未切分 [0, 8)
const view: EpisodesView = {
  unit: "chars",
  units: 0,
  cut_units: 0,
  episodes: [],
  unregistered: [],
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
    // 「尾」前的 UTF-16 下标是 5（😀 占两个代码单元），码位偏移是 4
    expect(pointInRun(0, runs[0].start, runs[0].text, 5)).toEqual({ file: 0, offset: 106 });
    expect(pointInRun(1, runs[1].start, runs[1].text, 99)).toEqual({ file: 1, offset: 111 });
  });

  it("切出集内部为拆分，未切分的原文上为切分，新集从这段未切分原文的开头起", () => {
    expect(resolvePointAction(view, { file: 0, offset: 5 })).toEqual({
      kind: "split",
      file: 0,
      episode: 1,
      start: 0,
      at: 5,
      end: 10,
    });
    expect(resolvePointAction(view, { file: 0, offset: 25 })).toEqual({ kind: "cut", file: 0, start: 20, end: 25 });
    expect(resolvePointAction(view, { file: 1, offset: 3 })).toEqual({ kind: "cut", file: 1, start: 0, end: 3 });
    expect(resolvePointAction(view, { file: 0, offset: 20 })).toBeNull();
    expect(resolvePointAction(view, { file: 1, offset: 0 })).toBeNull();
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
