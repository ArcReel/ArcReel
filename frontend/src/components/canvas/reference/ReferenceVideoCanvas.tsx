// frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx
import { useCallback, useEffect, useMemo } from "react";
import { useShallow } from "zustand/shallow";
import { useTranslation } from "react-i18next";
import { UnitList } from "./UnitList";
import { UnitPreviewPanel } from "./UnitPreviewPanel";
import { ReferenceVideoCard } from "./ReferenceVideoCard";
import { ReferencePanel } from "./ReferencePanel";
import { useReferenceVideoStore, referenceVideoCacheKey } from "@/stores/reference-video-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import type { ReferenceResource, ReferenceVideoUnit } from "@/types";

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
  const patchUnit = useReferenceVideoStore((s) => s.patchUnit);
  const generate = useReferenceVideoStore((s) => s.generate);
  const select = useReferenceVideoStore((s) => s.select);
  const updatePromptDebounced = useReferenceVideoStore((s) => s.updatePromptDebounced);
  const consumePendingPrompt = useReferenceVideoStore((s) => s.consumePendingPrompt);

  const units =
    useReferenceVideoStore((s) => s.unitsByEpisode[referenceVideoCacheKey(projectName, episode)]) ??
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

  const handlePromptChange = useCallback(
    (prompt: string, references: ReferenceResource[]) => {
      if (!selected) return;
      // prompt + references coalesce into one debounced PATCH — latest payload
      // wins, so rapid add-then-remove of an @mention cannot leak the stale
      // version to the server.
      updatePromptDebounced(projectName, episode, selected.unit_id, prompt, references);
    },
    [updatePromptDebounced, projectName, episode, selected],
  );

  // Panel actions must fold in any queued debounced prompt — otherwise the
  // pending PATCH would fire ~500ms later and overwrite `references` back to
  // their pre-panel-action value.
  const patchReferencesAtomic = useCallback(
    (unitId: string, nextRefs: ReferenceResource[]) => {
      const pendingPrompt = consumePendingPrompt(projectName, episode, unitId);
      const body =
        pendingPrompt !== undefined
          ? { prompt: pendingPrompt, references: nextRefs }
          : { references: nextRefs };
      void patchUnit(projectName, episode, unitId, body).catch((e) => {
        useAppStore.getState().pushToast(e instanceof Error ? e.message : String(e), "error");
      });
    },
    [consumePendingPrompt, patchUnit, projectName, episode],
  );

  const handleReorderRefs = useCallback(
    (next: ReferenceResource[]) => {
      if (!selected) return;
      patchReferencesAtomic(selected.unit_id, next);
    },
    [patchReferencesAtomic, selected],
  );

  const handleRemoveRef = useCallback(
    (ref: ReferenceResource) => {
      if (!selected) return;
      const next = selected.references.filter((r) => !(r.name === ref.name && r.type === ref.type));
      patchReferencesAtomic(selected.unit_id, next);
    },
    [patchReferencesAtomic, selected],
  );

  const handleAddRef = useCallback(
    (ref: ReferenceResource) => {
      if (!selected) return;
      if (selected.references.some((r) => r.type === ref.type && r.name === ref.name)) return;
      const next = [...selected.references, ref];
      patchReferencesAtomic(selected.unit_id, next);
    },
    [patchReferencesAtomic, selected],
  );

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-gray-800 px-4 py-2">
        <h2 className="text-sm font-semibold text-gray-100">
          <span translate="no">E{episode}</span>
          {episodeTitle ? `: ${episodeTitle}` : ""} · {t("reference_units_count", { count: units.length })}
        </h2>
        {error && (
          <p role="alert" className="mt-1 text-xs text-red-400">
            {error}
          </p>
        )}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(260px,20%)_1fr_minmax(280px,24%)] overflow-hidden">
        <UnitList
          units={units}
          selectedId={selectedUnitId}
          onSelect={select}
          onAdd={onAdd}
        />
        <div className="flex h-full min-h-0 flex-col overflow-hidden border-r border-gray-800 bg-gray-950/30">
          {selected ? (
            <>
              <div className="flex min-h-0 flex-1 flex-col p-3">
                <ReferenceVideoCard
                  unit={selected}
                  projectName={projectName}
                  episode={episode}
                  onChangePrompt={handlePromptChange}
                />
              </div>
              <ReferencePanel
                references={selected.references}
                projectName={projectName}
                onReorder={handleReorderRefs}
                onRemove={handleRemoveRef}
                onAdd={handleAddRef}
              />
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center text-xs text-gray-600">
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
