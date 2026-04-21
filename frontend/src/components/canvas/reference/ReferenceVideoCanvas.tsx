// frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/shallow";
import { useTranslation } from "react-i18next";
import { UnitList } from "./UnitList";
import { UnitPreviewPanel } from "./UnitPreviewPanel";
import { ReferenceVideoCard } from "./ReferenceVideoCard";
import { ReferencePanel } from "./ReferencePanel";
import { ReferenceUnitsPreprocessingCard } from "./ReferenceUnitsPreprocessingCard";
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

  // 默认选中第一个 unit；selectedUnitId 是全局单例（非 per-episode），
  // 切换 episode 后可能残留上一集的 unit_id，这里统一用 "是否在当前 units 里" 做合法性校验。
  useEffect(() => {
    if (units.length > 0 && !selected) {
      select(units[0].unit_id);
    }
  }, [units, selected, select]);

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

  // 小屏（<@4xl，容器 <896px）时把 editor / preview 压成 tab。@4xl+ 三栏时此状态被 CSS 忽略。
  const [smallTab, setSmallTab] = useState<"editor" | "preview">("editor");

  return (
    <div className="@container flex h-full flex-col">
      <div className="px-4 py-3">
        <h2 className="text-lg font-semibold text-gray-100">
          <span translate="no">E{episode}</span>
          {episodeTitle ? `: ${episodeTitle}` : ""}
        </h2>
        <p className="text-xs text-gray-500">{t("reference_units_count", { count: units.length })}</p>
        {error && (
          <p role="alert" className="mt-1 text-xs text-red-400">
            {error}
          </p>
        )}
      </div>
      <ReferenceUnitsPreprocessingCard
        projectName={projectName}
        episode={episode}
        unitCount={units.length}
      />
      {/* 外层 grid：<@md(448px) 单列；@md+ 双栏 (UnitList | 右侧 wrapper)。
          断点选 @md 是因为 agent chat 占右半屏时中栏常在 500-700px 区间，@2xl(672px) 错过太多场景。
          显式 grid-rows-1 + minmax(0,1fr) 防止隐式 row 被 UnitList 内容撑开破坏 min-h-0/overflow 链路。 */}
      <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,1fr)] overflow-hidden @md:grid-cols-[minmax(200px,30%)_1fr]">
        <UnitList
          units={units}
          selectedId={selectedUnitId}
          onSelect={select}
          onAdd={onAdd}
        />
        {/* 右侧 wrapper：<@4xl 用 flex column (tab + active panel)；@4xl+ 转 grid 两列 (editor | preview)。
            嵌套 grid 比 display:contents 更可靠，且避免浏览器对 contents + container query 变体的边缘行为。 */}
        <div className="flex min-h-0 flex-col overflow-hidden @4xl:grid @4xl:grid-cols-[1fr_minmax(260px,32%)] @4xl:grid-rows-[minmax(0,1fr)]">
          <div
            role="tablist"
            aria-label={t("reference_tab_aria")}
            className="flex gap-0 border-b border-gray-800 px-2 @4xl:hidden"
          >
            <button
              type="button"
              role="tab"
              aria-selected={smallTab === "editor"}
              onClick={() => setSmallTab("editor")}
              className={`rounded-t border-b-2 px-3 py-2 text-xs transition-colors focus-ring ${
                smallTab === "editor"
                  ? "border-indigo-500 font-medium text-indigo-400"
                  : "border-transparent text-gray-500 hover:text-gray-300"
              }`}
            >
              {t("reference_tab_editor")}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={smallTab === "preview"}
              onClick={() => setSmallTab("preview")}
              className={`rounded-t border-b-2 px-3 py-2 text-xs transition-colors focus-ring ${
                smallTab === "preview"
                  ? "border-indigo-500 font-medium text-indigo-400"
                  : "border-transparent text-gray-500 hover:text-gray-300"
              }`}
            >
              {t("reference_tab_preview")}
            </button>
          </div>
          <div
            className={`min-h-0 flex-1 flex-col overflow-hidden border-r border-gray-800 bg-gray-950/30 @4xl:flex ${
              smallTab === "editor" ? "flex" : "hidden"
            }`}
          >
            {selected ? (
              <>
                <div className="flex min-h-0 flex-1 flex-col p-3">
                  <ReferenceVideoCard
                    key={selected.unit_id}
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
          <div
            className={`min-h-0 overflow-hidden @4xl:block ${smallTab === "preview" ? "block" : "hidden"}`}
          >
            <UnitPreviewPanel
              unit={selected}
              projectName={projectName}
              onGenerate={onGenerateVoid}
              generating={generating}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
