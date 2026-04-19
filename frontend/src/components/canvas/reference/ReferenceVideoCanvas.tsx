import { useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { UnitList } from "./UnitList";
import { UnitPreviewPanel } from "./UnitPreviewPanel";
import { useReferenceVideoStore } from "@/stores/reference-video-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";

export interface ReferenceVideoCanvasProps {
  projectName: string;
  episode: number;
  episodeTitle?: string;
}

export function ReferenceVideoCanvas({ projectName, episode, episodeTitle }: ReferenceVideoCanvasProps) {
  const { t } = useTranslation("dashboard");

  const loadUnits = useReferenceVideoStore((s) => s.loadUnits);
  const addUnit = useReferenceVideoStore((s) => s.addUnit);
  const generate = useReferenceVideoStore((s) => s.generate);
  const select = useReferenceVideoStore((s) => s.select);
  const unitsByEpisode = useReferenceVideoStore((s) => s.unitsByEpisode);
  const units = unitsByEpisode[String(episode)] ?? [];
  const selectedUnitId = useReferenceVideoStore((s) => s.selectedUnitId);
  const error = useReferenceVideoStore((s) => s.error);

  const tasks = useTasksStore((s) => s.tasks);

  useEffect(() => {
    void loadUnits(projectName, episode);
  }, [loadUnits, projectName, episode]);

  const selected = useMemo(
    () => units.find((u) => u.unit_id === selectedUnitId) ?? null,
    [units, selectedUnitId],
  );

  const generating = useMemo(() => {
    if (!selected) return false;
    return tasks.some(
      (tk) =>
        tk.project_name === projectName &&
        tk.task_type === "reference_video" &&
        tk.resource_id === selected.unit_id &&
        (tk.status === "queued" || tk.status === "running"),
    );
  }, [tasks, projectName, selected]);

  const handleAdd = async () => {
    try {
      await addUnit(projectName, episode, { prompt: "", references: [] });
    } catch (e) {
      useAppStore.getState().pushToast(e instanceof Error ? e.message : String(e), "error");
    }
  };

  const handleGenerate = async (unitId: string) => {
    try {
      await generate(projectName, episode, unitId);
    } catch (e) {
      useAppStore.getState().pushToast(e instanceof Error ? e.message : String(e), "error");
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-gray-800 px-4 py-2">
        <h2 className="text-sm font-semibold text-gray-100">
          E{episode}
          {episodeTitle ? `: ${episodeTitle}` : ""} · {t("reference_units_count", { count: units.length })}
        </h2>
        {error && <p className="mt-1 text-xs text-red-400">{error}</p>}
      </div>
      <div className="grid flex-1 grid-cols-[minmax(260px,20%)_1fr_minmax(280px,24%)] overflow-hidden">
        <UnitList
          units={units}
          selectedId={selectedUnitId}
          onSelect={select}
          onAdd={() => void handleAdd()}
        />
        <div className="flex h-full items-center justify-center border-r border-gray-800 bg-gray-950/30 p-6 text-xs text-gray-600">
          {/* PR5 will render the prompt editor + MentionPicker here. */}
          {selected
            ? selected.shots.map((s, i) => (
                <pre key={i} className="whitespace-pre-wrap text-left text-gray-400">
                  {s.text}
                </pre>
              ))
            : t("reference_canvas_empty")}
        </div>
        <UnitPreviewPanel
          unit={selected}
          projectName={projectName}
          onGenerate={(id) => void handleGenerate(id)}
          generating={generating}
        />
      </div>
    </div>
  );
}
