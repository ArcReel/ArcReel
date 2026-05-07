export interface GridLayout {
  gridSize: "grid_4" | "grid_6" | "grid_9" | null;
  rows: number;
  cols: number;
  cellCount: number;
  batchCount: number;
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

export function computeGridSize(count: number, aspectRatio: string = "9:16"): GridLayout {
  if (count < 1) return { gridSize: null, rows: 0, cols: 0, cellCount: 0, batchCount: 0 };
  const [w, h] = aspectRatio.split(":").map(Number);
  const isHorizontal = w > h;
  const effective = Math.min(count, 9);

  let gridSize: "grid_4" | "grid_6" | "grid_9";
  let cellCount: number;
  let rows: number;
  let cols: number;

  if (effective <= 4) {
    gridSize = "grid_4";
    cellCount = 4;
    rows = 2;
    cols = 2;
  } else if (effective <= 6) {
    gridSize = "grid_6";
    cellCount = 6;
    rows = isHorizontal ? 3 : 2;
    cols = isHorizontal ? 2 : 3;
  } else {
    gridSize = "grid_9";
    cellCount = 9;
    rows = 3;
    cols = 3;
  }

  const batchCount = count > cellCount ? Math.ceil(count / cellCount) : 1;
  return { gridSize, rows, cols, cellCount, batchCount };
}
