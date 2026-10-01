export interface GridLayout {
  gridSize: "grid_4" | "grid_9" | "grid_16" | "grid_25" | null;
  rows: number;
  cols: number;
  cellCount: number;
  batchCount: number;
}

/**
 * 档位阶梯，与后端 lib/script/grid/layout.py 的 _GRID_LADDER 逐项对应:
 * 全部为 N×N 平方切分,单格比例恒等于整图比例(即项目视频比例)。
 * 哪几档可用由格数上限决定,上限取自后端 /grid-capability 的 max_cell_count
 * (4K 门控要经供应商解析才能定,前端不自行推导分辨率档)。
 */
const GRID_LADDER = [
  { cellCount: 4, gridSize: "grid_4", side: 2 },
  { cellCount: 9, gridSize: "grid_9", side: 3 },
  { cellCount: 16, gridSize: "grid_16", side: 4 },
  { cellCount: 25, gridSize: "grid_25", side: 5 },
] as const;

/** 拿不到后端上限时按门控生效展示(封顶 3×3),宁可少算批次也不虚报 */
const FALLBACK_MAX_CELL_COUNT = 9;

interface GridMatchRecord {
  id: string;
  episode: number;
  scene_ids: string[];
  created_at: string;
}

/**
 * 按当前布局为分组的每一块找到对应的宫格，与后端 grid_submission 的判定一致：
 * 分组按 computeGridSize 的格数顺序切块，一张宫格只有 scene_ids 与某一块逐项相同
 * （含顺序）才算这一块的宫格，同一块多次生成取 created_at 最新的一张。
 * 章节切分点、分镜顺序或组内增删改变分块后，旧宫格对不上任何一块，这一块显示为未生成。
 * 返回值按块的顺序排列，没有宫格的块不占位。
 */
export function matchGridsForGroup<G extends GridMatchRecord>(
  grids: G[],
  groupSceneIds: readonly string[],
  episode: number,
  maxCellCount?: number,
): G[] {
  const { cellCount } = computeGridSize(groupSceneIds.length, maxCellCount);
  const latestByChunk = new Map<string, G>();
  for (const g of grids) {
    if (g.episode !== episode) continue;
    const key = g.scene_ids.join("\u0000");
    const current = latestByChunk.get(key);
    if (!current || g.created_at.localeCompare(current.created_at) > 0) latestByChunk.set(key, g);
  }
  const matched: G[] = [];
  for (let start = 0; cellCount > 0 && start < groupSceneIds.length; start += cellCount) {
    const chunk = groupSceneIds.slice(start, start + cellCount);
    const g = latestByChunk.get(chunk.join("\u0000"));
    if (g) matched.push(g);
  }
  return matched;
}

export function groupBySegmentBreak<S extends { segment_break?: boolean }>(
  segments: S[],
): S[][] {
  const groups: S[][] = [];
  let current: S[] = [];
  for (const seg of segments) {
    if (seg.segment_break && current.length > 0) {
      groups.push(current);
      current = [];
    }
    current.push(seg);
  }
  if (current.length > 0) groups.push(current);
  return groups;
}

export function computeGridSize(
  count: number,
  maxCellCount: number = FALLBACK_MAX_CELL_COUNT
): GridLayout {
  if (count < 1) return { gridSize: null, rows: 0, cols: 0, cellCount: 0, batchCount: 0 };
  const effective = Math.min(count, maxCellCount);
  const { cellCount, gridSize, side } =
    GRID_LADDER.find((cfg) => effective <= cfg.cellCount) ?? GRID_LADDER[GRID_LADDER.length - 1];

  const batchCount = count > cellCount ? Math.ceil(count / cellCount) : 1;
  return { gridSize, rows: side, cols: side, cellCount, batchCount };
}
