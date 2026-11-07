import { useEffect, useMemo, useState } from "react";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useActiveResourceIds } from "@/stores/tasks-store";
import type { AssetSheetStatusRow, AssetSheetType } from "@/types";

/**
 * Manifest-based state for every project sheet, including derivatives.
 *
 * States compare manifest/canonical state through reads/fingerprints, separate from project payloads. Refetch on project-snapshot
 * revision changes (sheet writes/description edits) or sheet-task occupancy changes (starts/finishes).
 * Retain last values on failed same-project fetches; after project changes return only new-project states.
 */
export function useAssetSheetStatus(projectName: string, enabled = true): AssetSheetStatusRow[] {
  const [loaded, setLoaded] = useState<{ projectName: string; rows: AssetSheetStatusRow[] } | null>(null);
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
        if (!controller.signal.aborted) setLoaded({ projectName, rows: res.assets ?? [] });
      })
      .catch(() => {
        /* Failed/invalidated reads retain previous status. */
      });
    return () => controller.abort();
  }, [enabled, projectName, snapshotRevision, activeCount]);

  return loaded?.projectName === projectName ? loaded.rows : NO_ROWS;
}

const NO_ROWS: AssetSheetStatusRow[] = [];

/** State rows for base assets of one type, indexed by asset name. */
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

/**
 * Number submitted in one asset-type batch, plus number missing descriptions.
 *
 * Match server planning: pending bases with descriptions; derivatives additionally need usable base sheets or their bases
 * in this batch. Exclude generating assets, which previews list as skipped. Preview determines exact membership; buttons use counts only.
 */
export function pendingSheetCounts(
  rows: AssetSheetStatusRow[],
  assetType: AssetSheetType,
  isGenerating: (row: AssetSheetStatusRow) => boolean = () => false,
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
    if (row.status !== "missing" || isGenerating(row)) continue;
    if (row.description_missing) {
      missingDescription += 1;
    } else if (row.derivative === null || ownerUsable(row.name)) {
      generatable += 1;
    }
  }
  return { generatable, missingDescription };
}
