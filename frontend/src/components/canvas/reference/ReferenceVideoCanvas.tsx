// frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/shallow";
import { useTranslation } from "react-i18next";
import {
  ChevronLeft,
  ChevronRight,
  Clock,
  Loader2,
  Save,
  Scissors,
  Sparkles,
} from "lucide-react";
import { UnitList } from "./UnitList";
import { UnitRail } from "./UnitRail";
import { UnitPreviewPanel } from "./UnitPreviewPanel";
import { ReferenceVideoCard, unitPromptText } from "./ReferenceVideoCard";
import { ReferencePanel } from "./ReferencePanel";
import { EpisodeHeader } from "./EpisodeHeader";
import { PreprocessingView } from "@/components/canvas/timeline/PreprocessingView";
import {
  useReferenceVideoStore,
  referenceVideoCacheKey,
} from "@/stores/reference-video-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useCostStore } from "@/stores/cost-store";
import { errMsg } from "@/utils/async";
import { mergeReferences } from "@/utils/reference-mentions";
import type {
  CostBreakdown,
  ReferenceResource,
  ReferenceVideoUnit,
  TaskStatus,
  UnitStatus,
} from "@/types";

export interface ReferenceVideoCanvasProps {
  projectName: string;
  episode: number;
  episodeTitle?: string;
}

const EMPTY_UNITS: readonly ReferenceVideoUnit[] = Object.freeze([]);

// 容器宽度断点（px，对应设计稿的响应式行为）。
//   < LIST_RAIL_BREAKPOINT — 左侧 UnitList 收成 56px rail（带 flyout 触发）
//   < STACK_PREVIEW_BREAKPOINT — 中右合栏，预览叠成 sub-tab
const LIST_RAIL_BREAKPOINT = 1100;
const STACK_PREVIEW_BREAKPOINT = 880;
// 三栏布局下右栏宽度——主区更宽时给预览更大的呼吸空间。
const PREVIEW_COL_NARROW = 320;
const PREVIEW_COL_WIDE = 360;
const WIDE_BREAKPOINT = 1280;

/** 草稿 map 的复合键：`unit_id` 格式 `E{episode}U{n}` 在不同项目下会重复，所以必须
 *  把 project+episode 一同编入 key，否则切换项目会误把旧项目的未保存草稿应用到新项目
 *  同名 unit 上，造成跨项目数据污染。与 store 侧的 `_debounceKey` 约定一致。 */
function draftKey(projectName: string, episode: number, unitId: string): string {
  return `${projectName}::${episode}::${unitId}`;
}

/** Toast an error with tone="error". Optional `format` wraps the normalized
 *  message (e.g. an i18n template); without it the raw message is shown. */
function toastError(e: unknown, format?: (msg: string) => string): void {
  const msg = errMsg(e);
  useAppStore.getState().pushToast(format ? format(msg) : msg, "error");
}

function sumBreakdown(b: CostBreakdown | undefined): number {
  if (!b) return 0;
  let total = 0;
  for (const v of Object.values(b)) total += v;
  return total;
}

export function ReferenceVideoCanvas({
  projectName,
  episode,
  episodeTitle,
}: ReferenceVideoCanvasProps) {
  const { t } = useTranslation("dashboard");

  const loadUnits = useReferenceVideoStore((s) => s.loadUnits);
  const addUnit = useReferenceVideoStore((s) => s.addUnit);
  const patchUnit = useReferenceVideoStore((s) => s.patchUnit);
  const generate = useReferenceVideoStore((s) => s.generate);
  const select = useReferenceVideoStore((s) => s.select);

  const units =
    useReferenceVideoStore((s) => s.unitsByEpisode[referenceVideoCacheKey(projectName, episode)]) ??
    (EMPTY_UNITS as ReferenceVideoUnit[]);
  const selectedUnitId = useReferenceVideoStore((s) => s.selectedUnitId);
  const error = useReferenceVideoStore((s) => s.error);
  const loading = useReferenceVideoStore((s) => s.loading);
  const project = useProjectsStore((s) => s.currentProjectData);

  // Draft prompts keyed by unit_id — 只在 textarea 内容偏离服务端值时保留一条 entry。
  // 切换 unit 不清空，便于用户在多个 unit 间来回编辑而不丢失输入。
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

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

  // Optimistic UI 集合：见原版长注释。POST 前置位 → 队列接力 → 队列窗口换出后失效。
  const [optimisticUnitIds, setOptimisticUnitIds] = useState<Set<string>>(() => new Set());

  // 任务失败 toast：transition detection。仅在 prev !== failed && next === failed 触发。
  const prevTaskStatusRef = useRef<Map<string, TaskStatus>>(new Map());
  useEffect(() => {
    const prev = prevTaskStatusRef.current;
    const next = new Map<string, TaskStatus>();
    for (const tk of relevantTasks) {
      const before = prev.get(tk.task_id);
      if (tk.status === "failed" && before !== undefined && before !== "failed") {
        useAppStore.getState().pushNotification(
          t("reference_generation_task_failed", {
            unitId: tk.resource_id,
            reason: tk.error_message ?? t("reference_status_failed"),
          }),
          "error",
        );
      }
      next.set(tk.task_id, tk.status);
    }
    prevTaskStatusRef.current = next;
  }, [relevantTasks, t]);

  // Per-unit derived UI status — 给 UnitList / UnitRail / PreviewPanel 共用。
  const statusMap = useMemo<Record<string, UnitStatus>>(() => {
    const map: Record<string, UnitStatus> = {};
    for (const u of units) {
      let st: UnitStatus = u.generated_assets.video_clip ? "ready" : "pending";
      const queueRow = relevantTasks.find((tk) => tk.resource_id === u.unit_id);
      if (queueRow?.status === "queued" || queueRow?.status === "running") st = "running";
      else if (queueRow?.status === "failed") st = "failed";
      else if (optimisticUnitIds.has(u.unit_id) && !queueRow) st = "running";
      map[u.unit_id] = st;
    }
    return map;
  }, [units, relevantTasks, optimisticUnitIds]);

  const generating = !!(selected && statusMap[selected.unit_id] === "running");

  const failureMessage = useMemo(() => {
    if (!selected) return null;
    if (statusMap[selected.unit_id] !== "failed") return null;
    const tk = relevantTasks.find((x) => x.resource_id === selected.unit_id);
    return tk?.error_message ?? null;
  }, [selected, statusMap, relevantTasks]);

  // dirty 状态 map：仅扫当前 episode 的 units，drafts entry 与 baseText 不一致时记真。
  const dirtyMap = useMemo<Record<string, boolean>>(() => {
    const map: Record<string, boolean> = {};
    for (const u of units) {
      const v = drafts[draftKey(projectName, episode, u.unit_id)];
      if (v !== undefined && v !== unitPromptText(u)) map[u.unit_id] = true;
    }
    return map;
  }, [units, drafts, projectName, episode]);

  const handleAdd = useCallback(async () => {
    try {
      await addUnit(projectName, episode, { prompt: "", references: [] });
    } catch (e) {
      toastError(e);
    }
  }, [addUnit, projectName, episode]);

  // 小屏（cw < STACK_PREVIEW_BREAKPOINT）时把 editor / preview 压成 sub-tab。
  const [stackTab, setStackTab] = useState<"editor" | "preview">("editor");

  const handleGenerate = useCallback(
    async (unitId: string) => {
      setOptimisticUnitIds((s) => {
        if (s.has(unitId)) return s;
        const next = new Set(s);
        next.add(unitId);
        return next;
      });
      setStackTab("preview");
      try {
        const { deduped } = await generate(projectName, episode, unitId);
        useAppStore
          .getState()
          .pushToast(
            t(deduped ? "reference_generate_deduped" : "reference_generate_queued"),
            "info",
          );
      } catch (e) {
        setOptimisticUnitIds((s) => {
          if (!s.has(unitId)) return s;
          const next = new Set(s);
          next.delete(unitId);
          return next;
        });
        toastError(e, (msg) => t("reference_generate_request_failed", { error: msg }));
      }
    },
    [generate, projectName, episode, t],
  );

  const handleBatchGenerate = useCallback(async () => {
    const targets = units.filter((u) => statusMap[u.unit_id] === "pending");
    if (targets.length === 0) {
      useAppStore.getState().pushToast(t("reference_batch_nothing_to_do"), "info");
      return;
    }
    for (const u of targets) {
      // 串行 enqueue —— 让前端依次触发后端 dedup 检查；后端实际仍按 worker 并发跑。
      await handleGenerate(u.unit_id);
    }
  }, [units, statusMap, handleGenerate, t]);

  const onAdd = useCallback(() => void handleAdd(), [handleAdd]);
  const onGenerateVoid = useCallback((id: string) => void handleGenerate(id), [handleGenerate]);

  // Draft 管理：每次输入只更新本地 drafts，不触发网络请求。草稿与服务端值一致时
  // 自动清除该条目，避免"回退到原值后仍显示未保存"的误判。
  const handlePromptChange = useCallback(
    (next: string) => {
      if (!selected) return;
      const key = draftKey(projectName, episode, selected.unit_id);
      const baseText = unitPromptText(selected);
      setDrafts((d) => {
        if (next === baseText) {
          if (!(key in d)) return d;
          const copy = { ...d };
          delete copy[key];
          return copy;
        }
        return { ...d, [key]: next };
      });
    },
    [selected, projectName, episode],
  );

  const currentText = useMemo(() => {
    if (!selected) return "";
    const base = unitPromptText(selected);
    return drafts[draftKey(projectName, episode, selected.unit_id)] ?? base;
  }, [selected, drafts, projectName, episode]);

  const isDirty = !!(selected && dirtyMap[selected.unit_id]);

  // 全局"是否有任何未保存草稿"——用于 beforeunload 保护。
  const hasAnyDraft = Object.keys(drafts).length > 0;

  const handleSave = useCallback(async () => {
    if (!selected) return;
    const unitId = selected.unit_id;
    const key = draftKey(projectName, episode, unitId);
    const draftText = drafts[key];
    if (draftText === undefined || draftText === unitPromptText(selected)) return;
    const nextRefs = mergeReferences(draftText, selected.references, project ?? null);
    setSaving(true);
    try {
      await patchUnit(projectName, episode, unitId, {
        prompt: draftText,
        references: nextRefs,
      });
      setDrafts((d) => {
        if (d[key] !== draftText) return d;
        const copy = { ...d };
        delete copy[key];
        return copy;
      });
    } catch (e) {
      toastError(e);
    } finally {
      setSaving(false);
    }
  }, [selected, drafts, project, patchUnit, projectName, episode]);

  // 引用增删/排序保持即时保存；带上 pending prompt 草稿一同 patch。
  const patchReferencesAtomic = useCallback(
    (unitId: string, nextRefs: ReferenceResource[]) => {
      const key = draftKey(projectName, episode, unitId);
      const draftText = drafts[key];
      const unit = units.find((u) => u.unit_id === unitId);
      const hasDraft =
        draftText !== undefined && unit !== undefined && draftText !== unitPromptText(unit);
      const body: { prompt?: string; references: ReferenceResource[] } = hasDraft
        ? { prompt: draftText, references: nextRefs }
        : { references: nextRefs };
      void patchUnit(projectName, episode, unitId, body)
        .then(() => {
          if (!hasDraft) return;
          setDrafts((d) => {
            if (d[key] !== draftText) return d;
            const copy = { ...d };
            delete copy[key];
            return copy;
          });
        })
        .catch((e) => {
          toastError(e);
        });
    },
    [drafts, units, patchUnit, projectName, episode],
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
      const next = selected.references.filter(
        (r) => !(r.name === ref.name && r.type === ref.type),
      );
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

  // 主 Tab：units（视频单元工作台）/ preproc（拆分预处理）。切换 episode/project 时回到 units。
  const [tab, setTab] = useState<"units" | "preproc">("units");
  const [lastEpisode, setLastEpisode] = useState(episode);
  const [lastProject, setLastProject] = useState(projectName);
  if (lastEpisode !== episode || lastProject !== projectName) {
    setLastEpisode(episode);
    setLastProject(projectName);
    setTab("units");
  }

  // 预处理状态：用于 Tab 旁的小指示。
  const preprocStatus: "loading" | "error" | "empty" | "ready" = loading
    ? "loading"
    : error
      ? "error"
      : units.length === 0
        ? "empty"
        : "ready";
  const preprocDot: Record<typeof preprocStatus, string> = {
    loading: "bg-gray-500",
    error: "bg-red-500",
    empty: "bg-gray-500",
    ready: "bg-emerald-500",
  };

  // 任一 unit 有未保存草稿时，离开页面需警告。
  useEffect(() => {
    if (!hasAnyDraft) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [hasAnyDraft]);

  // ---------- 容器宽度感知（响应式断点） ----------
  const workbenchRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(1200);
  useLayoutEffect(() => {
    if (!workbenchRef.current) return;
    const el = workbenchRef.current;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) {
        const w = e.contentRect.width;
        // jsdom 测试环境下 contentRect.width 恒为 0；一旦回落到 0 会
        // 误触发 rail/stackPreview 路径。仅在拿到正常宽度时更新。
        if (w > 0) setContainerWidth(w);
      }
    });
    ro.observe(el);
    const initial = el.getBoundingClientRect().width;
    if (initial > 0) setContainerWidth(initial);
    return () => ro.disconnect();
  }, []);
  const listMode: "rail" | "full" = containerWidth < LIST_RAIL_BREAKPOINT ? "rail" : "full";
  const stackPreview = containerWidth < STACK_PREVIEW_BREAKPOINT;
  const listColW = listMode === "rail" ? 56 : 320;
  const previewColW = containerWidth < WIDE_BREAKPOINT ? PREVIEW_COL_NARROW : PREVIEW_COL_WIDE;
  const gridCols = stackPreview
    ? `${listColW}px minmax(0, 1fr)`
    : `${listColW}px minmax(0, 1fr) ${previewColW}px`;
  const [listFlyoutOpen, setListFlyoutOpen] = useState(false);

  // ---------- 单元导航 / 费用 ----------
  const epCost = useCostStore((s) => s._episodeIndex.get(episode));
  const segCost = useCostStore((s) =>
    selected ? s._segmentIndex.get(selected.unit_id) : undefined,
  );
  const estimatedCost = segCost ? sumBreakdown(segCost.estimate.video) : undefined;
  const actualCost = segCost ? sumBreakdown(segCost.actual.video) : undefined;
  // unused-var 守卫：epCost 用作 EpisodeHeader 内部派生的隐含订阅依赖（已在 store selector 触发），
  // 这里不直接使用；保留 import 让 fast refresh 友好。
  void epCost;

  const selectedIndex = selected ? units.findIndex((u) => u.unit_id === selected.unit_id) : -1;
  const goPrev = useCallback(() => {
    if (selectedIndex <= 0) return;
    select(units[selectedIndex - 1].unit_id);
  }, [select, units, selectedIndex]);
  const goNext = useCallback(() => {
    if (selectedIndex < 0 || selectedIndex >= units.length - 1) return;
    select(units[selectedIndex + 1].unit_id);
  }, [select, units, selectedIndex]);

  // ---------- 渲染 ----------
  return (
    <div className="flex h-full min-h-0 flex-col">
      <EpisodeHeader episode={episode} title={episodeTitle ?? `E${episode}`} units={units} />

      {/* Tab + 批量生成 */}
      <div
        role="tablist"
        aria-label={t("reference_main_tab_aria")}
        className="flex items-center gap-0.5 border-b border-[var(--color-hairline)] bg-[oklch(0.19_0.012_250_/_0.5)] px-5"
      >
        <button
          type="button"
          role="tab"
          aria-selected={tab === "units"}
          onClick={() => setTab("units")}
          className={`focus-ring relative px-3.5 py-2.5 text-[12.5px] font-medium ${
            tab === "units" ? "text-[var(--color-text)]" : "text-[var(--color-text-3)]"
          }`}
        >
          {t("reference_tab_units")}
          {tab === "units" && (
            <span
              aria-hidden="true"
              className="absolute -bottom-px left-2.5 right-2.5 h-0.5 rounded bg-[var(--color-accent)]"
            />
          )}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "preproc"}
          onClick={() => setTab("preproc")}
          className={`focus-ring relative inline-flex items-center gap-1.5 px-3.5 py-2.5 text-[12.5px] font-medium ${
            tab === "preproc" ? "text-[var(--color-text)]" : "text-[var(--color-text-3)]"
          }`}
        >
          <span>{t("reference_tab_preprocess")}</span>
          {preprocStatus === "loading" ? (
            <Loader2 className="h-3 w-3 animate-spin text-[var(--color-text-4)]" aria-hidden="true" />
          ) : (
            <span
              aria-hidden="true"
              className={`h-1.5 w-1.5 rounded-full ${preprocDot[preprocStatus]}`}
            />
          )}
          {tab === "preproc" && (
            <span
              aria-hidden="true"
              className="absolute -bottom-px left-2.5 right-2.5 h-0.5 rounded bg-[var(--color-accent)]"
            />
          )}
        </button>
        <span className="flex-1" />
        {tab === "units" && (
          <button
            type="button"
            onClick={() => void handleBatchGenerate()}
            disabled={units.length === 0 || generating}
            className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-[var(--color-hairline)] bg-[oklch(0.22_0.011_265_/_0.5)] px-2.5 py-1 text-[11.5px] text-[var(--color-text-2)] transition-colors hover:bg-[oklch(0.26_0.013_265_/_0.7)] hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{t("reference_batch_generate")}</span>
          </button>
        )}
      </div>

      {error && tab === "units" && (
        <p
          role="alert"
          className="border-b border-[var(--color-hairline-soft)] bg-red-500/10 px-5 py-2 text-xs text-red-400"
        >
          {error}
        </p>
      )}

      {tab === "preproc" ? (
        <div className="min-h-0 flex-1 overflow-auto bg-[oklch(0.18_0.011_250_/_0.25)]">
          <div className="mx-auto w-full max-w-3xl px-6 py-5">
            <PreprocessingView
              projectName={projectName}
              episode={episode}
              contentMode="reference_video"
              compact
            />
          </div>
        </div>
      ) : (
        <div
          ref={workbenchRef}
          className="relative min-h-0 flex-1 overflow-hidden bg-[oklch(0.18_0.011_250_/_0.25)]"
        >
          <div className="grid h-full min-h-0" style={{ gridTemplateColumns: gridCols }}>
            {/* 左：UnitList / UnitRail */}
            {listMode === "full" ? (
              <UnitList
                units={units}
                selectedId={selectedUnitId}
                onSelect={select}
                onAdd={onAdd}
                dirtyMap={dirtyMap}
                statusMap={statusMap}
              />
            ) : (
              <UnitRail
                units={units}
                selectedId={selectedUnitId}
                onSelect={select}
                onExpand={() => setListFlyoutOpen(true)}
                dirtyMap={dirtyMap}
                statusMap={statusMap}
              />
            )}

            {/* 中：UnitHeader + Editor / Preview（stackPreview 时叠 sub-tab） */}
            <div className="flex min-h-0 flex-col overflow-hidden bg-[radial-gradient(ellipse_at_top,oklch(0.20_0.012_270_/_0.35),oklch(0.17_0.010_265_/_0.2))]">
              {selected ? (
                <>
                  <div className="flex flex-wrap items-center gap-2 border-b border-[var(--color-hairline-soft)] px-4 py-2.5">
                    <span
                      translate="no"
                      className="rounded px-2.5 py-1 font-mono text-xs font-bold tracking-wider text-[oklch(0.14_0_0)] [background:linear-gradient(180deg,var(--color-accent-2),var(--color-accent))] shadow-[inset_0_1px_0_oklch(1_0_0_/_0.3),0_2px_6px_-2px_var(--color-accent-glow)]"
                    >
                      {selected.unit_id}
                    </span>
                    <span className="inline-flex items-center gap-1 rounded border border-[var(--color-hairline-soft)] bg-[oklch(0.22_0.011_265_/_0.6)] px-2 py-0.5 text-[11.5px] text-[var(--color-text-2)]">
                      <Clock className="h-3 w-3" aria-hidden="true" />
                      <span className="font-mono tabular-nums">{selected.duration_seconds}s</span>
                    </span>
                    <span className="inline-flex items-center gap-1 rounded border border-[var(--color-hairline-soft)] bg-[oklch(0.22_0.011_265_/_0.6)] px-2 py-0.5 text-[11.5px] text-[var(--color-text-2)]">
                      <Scissors className="h-3 w-3" aria-hidden="true" />
                      <span className="font-mono tabular-nums">
                        {t("reference_unit_shots_count", { count: selected.shots.length })}
                      </span>
                    </span>
                    <span className="flex-1" />
                    {selectedIndex >= 0 && (
                      <span className="font-mono text-[10.5px] tabular-nums text-[var(--color-text-4)]">
                        {selectedIndex + 1} / {units.length}
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={goPrev}
                      disabled={selectedIndex <= 0}
                      aria-label={t("reference_unit_prev")}
                      className="focus-ring inline-grid h-6 w-6 place-items-center rounded border border-[var(--color-hairline)] bg-[oklch(0.22_0.011_265_/_0.5)] text-[var(--color-text-2)] hover:bg-[oklch(0.26_0.013_265_/_0.7)] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      onClick={goNext}
                      disabled={selectedIndex < 0 || selectedIndex >= units.length - 1}
                      aria-label={t("reference_unit_next")}
                      className="focus-ring inline-grid h-6 w-6 place-items-center rounded border border-[var(--color-hairline)] bg-[oklch(0.22_0.011_265_/_0.5)] text-[var(--color-text-2)] hover:bg-[oklch(0.26_0.013_265_/_0.7)] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </div>

                  {stackPreview && (
                    <div
                      role="tablist"
                      aria-label={t("reference_tab_aria")}
                      className="flex items-center gap-0 border-b border-[var(--color-hairline)] bg-[oklch(0.19_0.012_250_/_0.4)] px-5"
                    >
                      <button
                        type="button"
                        role="tab"
                        aria-selected={stackTab === "editor"}
                        onClick={() => setStackTab("editor")}
                        className={`focus-ring relative inline-flex items-center gap-1.5 px-3.5 py-2.5 text-[12.5px] font-medium ${
                          stackTab === "editor"
                            ? "text-[var(--color-text)]"
                            : "text-[var(--color-text-3)]"
                        }`}
                      >
                        <Scissors className="h-3 w-3" aria-hidden="true" />
                        <span>{t("reference_tab_editor")}</span>
                        {isDirty && (
                          <span
                            aria-label={t("reference_tab_dirty_aria")}
                            className="h-1.5 w-1.5 rounded-full bg-amber-400"
                          />
                        )}
                        {stackTab === "editor" && (
                          <span
                            aria-hidden="true"
                            className="absolute -bottom-px left-2.5 right-2.5 h-0.5 rounded bg-[var(--color-accent)]"
                          />
                        )}
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={stackTab === "preview"}
                        onClick={() => setStackTab("preview")}
                        className={`focus-ring relative inline-flex items-center gap-1.5 px-3.5 py-2.5 text-[12.5px] font-medium ${
                          stackTab === "preview"
                            ? "text-[var(--color-text)]"
                            : "text-[var(--color-text-3)]"
                        }`}
                      >
                        <span>{t("reference_tab_preview")}</span>
                        {statusMap[selected.unit_id] === "running" && (
                          <span className="h-1.5 w-1.5 rounded-full bg-amber-400 motion-safe:animate-pulse" />
                        )}
                        {statusMap[selected.unit_id] === "ready" && (
                          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                        )}
                        {statusMap[selected.unit_id] === "failed" && (
                          <span className="h-1.5 w-1.5 rounded-full bg-red-400" />
                        )}
                        {stackTab === "preview" && (
                          <span
                            aria-hidden="true"
                            className="absolute -bottom-px left-2.5 right-2.5 h-0.5 rounded bg-[var(--color-accent)]"
                          />
                        )}
                      </button>
                    </div>
                  )}

                  <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                    {(!stackPreview || stackTab === "editor") && (
                      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                        <ReferencePanel
                          references={selected.references}
                          projectName={projectName}
                          onReorder={handleReorderRefs}
                          onRemove={handleRemoveRef}
                          onAdd={handleAddRef}
                        />
                        <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-3">
                          <ReferenceVideoCard
                            key={selected.unit_id}
                            unit={selected}
                            projectName={projectName}
                            episode={episode}
                            value={currentText}
                            onChange={handlePromptChange}
                          />
                        </div>
                        {/* Editor bottom bar */}
                        <div className="flex flex-shrink-0 items-center gap-2 border-t border-[var(--color-hairline-soft)] bg-[oklch(0.18_0.010_265_/_0.5)] px-3.5 py-2">
                          <span
                            className={`inline-flex items-center gap-1.5 text-[11px] ${
                              isDirty ? "text-amber-300" : "text-[var(--color-text-4)]"
                            }`}
                          >
                            {isDirty ? (
                              <>
                                <span
                                  aria-hidden="true"
                                  className="h-1.5 w-1.5 rounded-full bg-amber-400"
                                />
                                {t("reference_unsaved")}
                              </>
                            ) : (
                              <>
                                <span aria-hidden="true" className="text-emerald-400">
                                  ●
                                </span>
                                {t("reference_synced")}
                              </>
                            )}
                          </span>
                          <span className="flex-1" />
                          <button
                            type="button"
                            onClick={() => void handleSave()}
                            disabled={!isDirty || saving}
                            className={`focus-ring inline-flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-semibold ${
                              isDirty
                                ? "text-[oklch(0.14_0_0)] [background:linear-gradient(180deg,var(--color-accent-2),var(--color-accent))] shadow-[inset_0_1px_0_oklch(1_0_0_/_0.3),0_4px_12px_-4px_var(--color-accent-glow)]"
                                : "border border-[var(--color-hairline)] bg-[oklch(0.22_0.011_265_/_0.5)] text-[var(--color-text-4)]"
                            } disabled:cursor-not-allowed`}
                          >
                            {saving ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                            ) : (
                              <Save className="h-3.5 w-3.5" aria-hidden="true" />
                            )}
                            {saving ? t("common:saving") : t("common:save")}
                          </button>
                        </div>
                      </div>
                    )}
                    {stackPreview && stackTab === "preview" && (
                      <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-[linear-gradient(180deg,oklch(0.19_0.011_265_/_0.5),oklch(0.17_0.010_265_/_0.35))]">
                        <UnitPreviewPanel
                          unit={selected}
                          projectName={projectName}
                          status={statusMap[selected.unit_id]}
                          generating={generating}
                          errorMessage={failureMessage}
                          estimatedCost={estimatedCost}
                          actualCost={actualCost}
                          onGenerate={onGenerateVoid}
                        />
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <div className="flex flex-1 items-center justify-center text-xs text-[var(--color-text-4)]">
                  {t("reference_canvas_empty")}
                </div>
              )}
            </div>

            {/* 右：UnitPreviewPanel（仅大屏） */}
            {!stackPreview && (
              <div className="flex min-h-0 flex-col overflow-hidden border-l border-[var(--color-hairline)] bg-[linear-gradient(180deg,oklch(0.19_0.011_265_/_0.5),oklch(0.17_0.010_265_/_0.35))]">
                <UnitPreviewPanel
                  unit={selected}
                  projectName={projectName}
                  status={selected ? statusMap[selected.unit_id] : undefined}
                  generating={generating}
                  errorMessage={failureMessage}
                  estimatedCost={estimatedCost}
                  actualCost={actualCost}
                  onGenerate={onGenerateVoid}
                />
              </div>
            )}
          </div>

          {/* 折叠态下的展开抽屉 */}
          {listFlyoutOpen && (
            <>
              <button
                type="button"
                aria-label={t("common:close")}
                onClick={() => setListFlyoutOpen(false)}
                className="absolute inset-0 z-30 bg-black/40 backdrop-blur-[2px]"
              />
              <div
                className="absolute bottom-0 left-0 top-0 z-40 w-[320px] shadow-[8px_0_24px_-8px_oklch(0_0_0_/_0.6)]"
              >
                <UnitList
                  units={units}
                  selectedId={selectedUnitId}
                  onSelect={(id) => {
                    select(id);
                    setListFlyoutOpen(false);
                  }}
                  onAdd={onAdd}
                  dirtyMap={dirtyMap}
                  statusMap={statusMap}
                />
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
