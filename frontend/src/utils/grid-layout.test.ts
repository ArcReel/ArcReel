import { describe, expect, it } from "vitest";
import { matchGridsForGroup } from "./grid-layout";

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
