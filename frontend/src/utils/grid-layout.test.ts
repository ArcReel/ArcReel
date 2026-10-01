import { describe, expect, it } from "vitest";
import { computeGridSize, matchGridsForGroup } from "./grid-layout";

interface FakeGrid {
  id: string;
  episode: number;
  scene_ids: string[];
  created_at: string;
}

function grid(
  id: string,
  scene_ids: string[],
  created_at: string,
  episode = 1,
): FakeGrid {
  return { id, episode, scene_ids, created_at };
}

// 阶梯必须与后端 lib/script/grid/layout.py 一致,否则批次预览数与实际入队张数会漂移
describe("computeGridSize", () => {
  it.each([
    [1, "grid_4", 2],
    [4, "grid_4", 2],
    [5, "grid_9", 3],
    [6, "grid_9", 3],
    [9, "grid_9", 3],
  ])("picks a square layout for %i scenes", (count, gridSize, side) => {
    const layout = computeGridSize(count);
    expect(layout.gridSize).toBe(gridSize);
    expect([layout.rows, layout.cols]).toEqual([side, side]);
    expect(layout.cellCount).toBe(side * side);
  });

  it.each([
    [10, "grid_16", 4],
    [16, "grid_16", 4],
    [17, "grid_25", 5],
    [25, "grid_25", 5],
  ])("uses the %i-scene large layout when the backend raises the cap", (count, gridSize, side) => {
    const layout = computeGridSize(count, 25);
    expect(layout.gridSize).toBe(gridSize);
    expect([layout.rows, layout.cols]).toEqual([side, side]);
    expect(layout.batchCount).toBe(1);
  });

  it.each([10, 17, 30])("caps at 3×3 for %i scenes under the gated cap", (count) => {
    const layout = computeGridSize(count, 9);
    expect(layout.gridSize).toBe("grid_9");
    expect(layout.cellCount).toBe(9);
    expect(layout.batchCount).toBe(Math.ceil(count / 9));
  });

  it("falls back to the gated cap when the backend cap is unknown", () => {
    expect(computeGridSize(16, undefined).gridSize).toBe("grid_9");
  });

  it("chunks beyond the largest layout", () => {
    expect(computeGridSize(30, 25).batchCount).toBe(2);
  });

  it("returns a null layout for an empty group", () => {
    expect(computeGridSize(0)).toEqual({
      gridSize: null,
      rows: 0,
      cols: 0,
      cellCount: 0,
      batchCount: 0,
    });
  });
});

describe("matchGridsForGroup", () => {
  it("matches a single grid covering the whole group exactly", () => {
    const grids = [grid("g1", ["s1", "s2", "s3"], "2026-05-01T00:00:00Z")];
    const result = matchGridsForGroup(grids, ["s1", "s2", "s3"], 1);
    expect(result.map((g) => g.id)).toEqual(["g1"]);
  });

  it("matches one grid per chunk when the group exceeds the cell cap (14 scenes → 9 + 5)", () => {
    const big = Array.from({ length: 14 }, (_, i) => `s${i + 1}`);
    const grids = [
      grid("tail", big.slice(9), "2026-05-01T00:00:00Z"),
      grid("head", big.slice(0, 9), "2026-05-01T00:00:01Z"),
    ];
    const result = matchGridsForGroup(grids, big, 1, 9);
    expect(result.map((g) => g.id)).toEqual(["head", "tail"]);
  });

  it("ignores grids belonging to a different episode", () => {
    const grids = [
      grid("g1", ["s1", "s2"], "2026-05-01T00:00:00Z", 1),
      grid("g2", ["s1", "s2"], "2026-05-01T00:00:00Z", 2),
    ];
    const result = matchGridsForGroup(grids, ["s1", "s2"], 1);
    expect(result.map((g) => g.id)).toEqual(["g1"]);
  });

  it("keeps the latest regeneration of the same chunk", () => {
    const grids = [
      grid("old", ["s1", "s2"], "2026-05-01T00:00:00Z"),
      grid("new", ["s1", "s2"], "2026-05-02T00:00:00Z"),
    ];
    const result = matchGridsForGroup(grids, ["s1", "s2"], 1);
    expect(result.map((g) => g.id)).toEqual(["new"]);
  });

  it("treats a group split by a new chapter break as not generated", () => {
    const grids = [grid("before_split", ["s1", "s2", "s3"], "2026-05-01T00:00:00Z")];
    expect(matchGridsForGroup(grids, ["s1", "s2"], 1)).toEqual([]);
    expect(matchGridsForGroup(grids, ["s3"], 1)).toEqual([]);
  });

  it("treats groups merged by a removed chapter break as not generated", () => {
    const grids = [
      grid("first_half", ["s1", "s2"], "2026-05-01T00:00:00Z"),
      grid("second_half", ["s3", "s4"], "2026-05-01T00:00:01Z"),
    ];
    expect(matchGridsForGroup(grids, ["s1", "s2", "s3", "s4"], 1, 9)).toEqual([]);
  });

  it("treats a reordered group as not generated", () => {
    const grids = [grid("g1", ["s1", "s2", "s3"], "2026-05-01T00:00:00Z")];
    expect(matchGridsForGroup(grids, ["s2", "s1", "s3"], 1)).toEqual([]);
  });

  it("matches only the chunks that still line up", () => {
    const big = Array.from({ length: 11 }, (_, i) => `s${i + 1}`);
    const grids = [
      grid("head", big.slice(0, 9), "2026-05-01T00:00:00Z"),
      grid("stale_tail", ["s10", "s12"], "2026-05-01T00:00:01Z"),
    ];
    const result = matchGridsForGroup(grids, big, 1, 9);
    expect(result.map((g) => g.id)).toEqual(["head"]);
  });
});
