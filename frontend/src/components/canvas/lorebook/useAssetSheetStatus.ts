import { useEffect, useMemo, useState } from "react";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useActiveResourceIds } from "@/stores/tasks-store";
import type { AssetSheetStatusRow, AssetSheetType } from "@/types";

/**
 * 项目里每张资产图（含衍生）按产物清单判定的状态。
 *
 * 状态是清单与规范状态的比对（读文件、算指纹），不随项目数据下发，故单独取：项目快照
 * 修订号变化（资产图写入、描述修改）或资产图任务的占用集变化（有任务开始或结束）时重取。
 * 取不到时保留上一次的值。
 */
export function useAssetSheetStatus(projectName: string, enabled = true): AssetSheetStatusRow[] {
  const [rows, setRows] = useState<AssetSheetStatusRow[]>([]);
  const snapshotRevision = useProjectsStore((s) => s.projectSnapshotRevisions[projectName] ?? 0);
  const activeCount =
    useActiveResourceIds("character", projectName).size +
    useActiveResourceIds("character_derivative", projectName).size +
    useActiveResourceIds("scene", projectName).size +
    useActiveResourceIds("prop", projectName).size +
    useActiveResourceIds("product", projectName).size;

  useEffect(() => {
    if (!enabled || !projectName) return;
    const controller = new AbortController();
    void API.getAssetSheetStatus(projectName, { signal: controller.signal })
      .then((res) => {
        if (!controller.signal.aborted) setRows(res.assets ?? []);
      })
      .catch(() => {
        /* 读取失败（含被作废）保留上一次的状态 */
      });
    return () => controller.abort();
  }, [enabled, projectName, snapshotRevision, activeCount]);

  return rows;
}

/** 某一类资产本体的状态行，按资产名索引。 */
export function useSheetStatusByName(
  rows: AssetSheetStatusRow[],
  assetType: AssetSheetType,
): Map<string, AssetSheetStatusRow> {
  return useMemo(
    () =>
      new Map(
        rows
          .filter((row) => row.asset_type === assetType && row.derivative === null)
          .map((row) => [row.name, row]),
      ),
    [rows, assetType],
  );
}

export type SheetStatusFilter = "all" | "pending" | "stale";

/** 按画廊筛选保留资产：待生成即产物清单判 missing，过期即 stale。 */
export function matchesSheetFilter(row: AssetSheetStatusRow | undefined, filter: SheetStatusFilter): boolean {
  if (filter === "all") return true;
  if (!row) return false;
  return filter === "pending" ? row.status === "missing" : row.status === "stale";
}

/**
 * 一类资产里一次批量生成会提交几张、另有几张缺描述。
 *
 * 与服务端规划同一口径：待生成且有描述的本体；衍生还要求本体资产图可用，或本体本身也在
 * 这一批里。精确名单以预览为准，这里只给按钮上的计数。
 */
export function pendingSheetCounts(
  rows: AssetSheetStatusRow[],
  assetType: AssetSheetType,
): { generatable: number; missingDescription: number } {
  const ofType = rows.filter((row) => row.asset_type === assetType);
  const owners = new Map(ofType.filter((row) => row.derivative === null).map((row) => [row.name, row]));
  const ownerUsable = (name: string) => {
    const owner = owners.get(name);
    if (!owner) return false;
    if (owner.status === "current" || owner.status === "stale") return true;
    return owner.status === "missing" && !owner.description_missing;
  };
  let generatable = 0;
  let missingDescription = 0;
  for (const row of ofType) {
    if (row.status !== "missing") continue;
    if (row.description_missing) {
      missingDescription += 1;
    } else if (row.derivative === null || ownerUsable(row.name)) {
      generatable += 1;
    }
  }
  return { generatable, missingDescription };
}
