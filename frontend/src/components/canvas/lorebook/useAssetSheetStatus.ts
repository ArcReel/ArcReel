import { useEffect, useMemo, useState } from "react";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useActiveResourceIds } from "@/stores/tasks-store";
import type { AssetSheetStatusRow, AssetSheetType } from "@/types";

export function useAssetSheetStatus(
  projectName: string,
  enabled = true,
  include?: "derivative_references",
): AssetSheetStatusRow[] {
  const [loaded, setLoaded] = useState<{
    projectName: string;
    include?: "derivative_references";
    revision: number;
    rows: AssetSheetStatusRow[];
  } | null>(null);
  const revision = useProjectsStore((s) =>
    include
      ? (s.projectSnapshotRevisions[projectName] ?? 0)
      : (s.assetRevisions[projectName] ?? s.projectSnapshotRevisions[projectName] ?? 0),
  );
  const activeCount =
    useActiveResourceIds("character", projectName).size +
    useActiveResourceIds("character_derivative", projectName).size +
    useActiveResourceIds("scene", projectName).size +
    useActiveResourceIds("prop", projectName).size +
    useActiveResourceIds("product", projectName).size;

  useEffect(() => {
    if (!enabled || !projectName) return;
    const controller = new AbortController();
    void API.getAssetSheetStatus(projectName, { signal: controller.signal, include })
      .then((res) => {
        if (!controller.signal.aborted) setLoaded({ projectName, include, revision, rows: res.assets ?? [] });
      })
      .catch(() => {
        if (include && !controller.signal.aborted) {
          setLoaded((prev) => prev?.projectName === projectName && prev.include === include
            ? { ...prev, rows: prev.rows.map((row) => row.referenced === undefined ? row : { ...row, referenced: null }) }
            : prev);
        }
      });
    return () => controller.abort();
  }, [enabled, projectName, include, revision, activeCount]);

  return useMemo(() => {
    if (loaded?.projectName !== projectName || loaded.include !== include) return NO_ROWS;
    if (!include || loaded.revision === revision) return loaded.rows;
    return loaded.rows.map((row) => row.referenced === undefined ? row : { ...row, referenced: null });
  }, [loaded, projectName, include, revision]);
}

const NO_ROWS: AssetSheetStatusRow[] = [];

export function useDerivativeReferencesByCharacter(
  rows: AssetSheetStatusRow[],
): Map<string, Map<string, boolean | null>> {
  return useMemo(() => {
    const byCharacter = new Map<string, Map<string, boolean | null>>();
    for (const row of rows) {
      if (row.asset_type !== "character" || row.derivative === null || row.referenced === undefined) continue;
      let inner = byCharacter.get(row.name);
      if (!inner) {
        inner = new Map<string, boolean | null>();
        byCharacter.set(row.name, inner);
      }
      inner.set(row.derivative, row.referenced);
    }
    return byCharacter;
  }, [rows]);
}

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
