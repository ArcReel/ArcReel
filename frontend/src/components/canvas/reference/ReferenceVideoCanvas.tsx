import { useCallback, useEffect, useMemo } from "react";
import { useShallow } from "zustand/shallow";
import { useTranslation } from "react-i18next";
import { UnitList } from "./UnitList";
import { UnitPreviewPanel } from "./UnitPreviewPanel";
import { useReferenceVideoStore } from "@/stores/reference-video-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import type { ReferenceVideoUnit } from "@/types";

export interface ReferenceVideoCanvasProps {
  projectName: string;
  episode: number;
  episodeTitle?: string;
}

const EMPTY_UNITS: readonly ReferenceVideoUnit[] = Object.freeze([]);

export function ReferenceVideoCanvas({ projectName, episode, episodeTitle }: ReferenceVideoCanvasProps) {
  const { t } = useTranslation("dashboard");

  const loadUnits = useReferenceVideoStore((s) => s.loadUnits);
  const addUnit = useReferenceVideoStore((s) => s.addUnit);
  const generate = useReferenceVideoStore((s) => s.generate);
  const select = useReferenceVideoStore((s) => s.select);
  // Narrow selector: only rerender when this episode's slice changes, not
  // sibling episodes' mutations.
  const units =
    useReferenceVideoStore((s) => s.unitsByEpisode[String(episode)]) ??
    (EMPTY_UNITS as ReferenceVideoUnit[]);
  const selectedUnitId = useReferenceVideoStore((s) => s.selectedUnitId);
  const error = useReferenceVideoStore((s) => s.error);

  const relevantTasks = useTasksStore(
    useShallow((s) =>
      s.tasks.filter(
        (tk) => tk.project_name === projectName && tk.task_type === "reference_video",
      ),
    ),
  );

  useEffect(() => {
    void loadUnits(projectName, episode);
  }, [loadUnits, projectName, episode]);

  const selected = useMemo(
    () => units.find((u) => u.unit_id === selectedUnitId) ?? null,
    [units, selectedUnitId],
  );

  const generating = useMemo(() => {
    if (!selected) return false;
    return relevantTasks.some(
      (tk) =>
        tk.resource_id === selected.unit_id &&
        (tk.status === "queued" || tk.status === "running"),
    );
  }, [relevantTasks, selected]);

  const handleAdd = useCallback(async () => {
    try {
      await addUnit(projectName, episode, { prompt: "", references: [] });
    } catch (e) {
      useAppStore.getState().pushToast(e instanceof Error ? e.message : String(e), "error");
    }
  }, [addUnit, projectName, episode]);

  const handleGenerate = useCallback(
    async (unitId: string) => {
      try {
        await generate(projectName, episode, unitId);
      } catch (e) {
        useAppStore.getState().pushToast(e instanceof Error ? e.message : String(e), "error");
      }
    },
    [generate, projectName, episode],
  );

  const onAdd = useCallback(() => void handleAdd(), [handleAdd]);
  const onGenerateVoid = useCallback((id: string) => void handleGenerate(id), [handleGenerate]);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-gray-800 px-4 py-2">
        <h2 className="text-sm font-semibold text-gray-100">
          <span translate="no">E{episode}</span>
          {episodeTitle ? `: ${episodeTitle}` : ""} · {t("reference_units_count", { count: units.length })}
        </h2>
        {error && <p className="mt-1 text-xs text-red-400">{error}</p>}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(260px,20%)_1fr_minmax(280px,24%)] overflow-hidden">
        <UnitList
          units={units}
          selectedId={selectedUnitId}
          onSelect={select}
          onAdd={onAdd}
        />
        <div className="flex h-full min-h-0 flex-col items-stretch justify-start overflow-y-auto border-r border-gray-800 bg-gray-950/30 p-6 text-xs text-gray-600">
          {selected ? (
            <div className="flex flex-col gap-3">
              {selected.shots.map((s, i) => (
                <pre key={i} className="whitespace-pre-wrap break-words text-left text-gray-400">
                  {s.text}
                </pre>
              ))}
            </div>
          ) : (
            <div className="flex flex-1 items-center justify-center">
              {t("reference_canvas_empty")}
            </div>
          )}
        </div>
        <UnitPreviewPanel
          unit={selected}
          projectName={projectName}
          onGenerate={onGenerateVoid}
          generating={generating}
        />
      </div>
    </div>
  );
}
