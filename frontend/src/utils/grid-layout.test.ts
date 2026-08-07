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

// 阶梯必须与后端 lib/grid/layout.py 一致,否则批次预览数与实际入队张数会漂移
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

  it("matches multiple chunk grids when group exceeds cell_count (regression: 14-scene group → grid_9 + grid_4)", () => {
    const big = Array.from({ length: 14 }, (_, i) => `s${i + 1}`);
    const grids = [
      grid("g9", big.slice(0, 9), "2026-05-01T00:00:00Z"),
      grid("g4", big.slice(9), "2026-05-01T00:00:01Z"),
    ];
    const result = matchGridsForGroup(grids, big, 1);
    expect(result.map((g) => g.id)).toEqual(["g9", "g4"]);
  });

  it("ignores grids belonging to a different episode", () => {
    const grids = [
      grid("g1", ["s1", "s2"], "2026-05-01T00:00:00Z", 1),
      grid("g2", ["s1", "s2"], "2026-05-01T00:00:00Z", 2),
    ];
    const result = matchGridsForGroup(grids, ["s1", "s2"], 1);
    expect(result.map((g) => g.id)).toEqual(["g1"]);
  });

  it("ignores grids whose scene_ids contain ids outside the group", () => {
    const grids = [
      grid("g1", ["s1", "s2"], "2026-05-01T00:00:00Z"),
      grid("g_other", ["s1", "s99"], "2026-05-01T00:00:01Z"),
    ];
    const result = matchGridsForGroup(grids, ["s1", "s2"], 1);
    expect(result.map((g) => g.id)).toEqual(["g1"]);
  });

  it("dedupes regenerations by scene_ids set, keeping latest created_at", () => {
    const grids = [
      grid("old", ["s1", "s2"], "2026-05-01T00:00:00Z"),
      grid("new", ["s1", "s2"], "2026-05-02T00:00:00Z"),
    ];
    const result = matchGridsForGroup(grids, ["s1", "s2"], 1);
    expect(result.map((g) => g.id)).toEqual(["new"]);
  });

  it("returns chunks ordered by created_at ascending", () => {
    const big = Array.from({ length: 14 }, (_, i) => `s${i + 1}`);
    const grids = [
      grid("late", big.slice(9), "2026-05-01T00:00:05Z"),
      grid("early", big.slice(0, 9), "2026-05-01T00:00:00Z"),
    ];
    const result = matchGridsForGroup(grids, big, 1);
    expect(result.map((g) => g.id)).toEqual(["early", "late"]);
  });

  it("returns empty for unrelated grids", () => {
    const grids = [grid("g1", ["x1"], "2026-05-01T00:00:00Z")];
    const result = matchGridsForGroup(grids, ["s1", "s2"], 1);
    expect(result).toEqual([]);
  });

  it("filters out obsolete overlapping grids covered by newer generations", () => {
    // 用户调整 segment_break 后,旧 chunk 仍在表里但不再属于当前布局。
    // 贪心覆盖按 created_at 降序,只保留贡献新 scene_id 的 grid。
    const grids = [
      grid("obsolete_subset", ["s1", "s2"], "2026-05-01T00:00:00Z"),
      grid("obsolete_superset", ["s1", "s2", "s3", "s4", "s5"], "2026-05-01T00:00:01Z"),
      grid("new_chunk_1", ["s1", "s2", "s3"], "2026-05-02T00:00:00Z"),
      grid("new_chunk_2", ["s4", "s5"], "2026-05-02T00:00:01Z"),
    ];
    const result = matchGridsForGroup(grids, ["s1", "s2", "s3", "s4", "s5"], 1);
    expect(result.map((g) => g.id)).toEqual(["new_chunk_1", "new_chunk_2"]);
  });
});
