// frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/shallow";
import { useTranslation } from "react-i18next";
import { ArrowLeft, ChevronRight, Edit3, Loader2, Save, X as XIcon } from "lucide-react";
import { UnitList } from "./UnitList";
import { UnitPreviewPanel } from "./UnitPreviewPanel";
import { ReferenceVideoCard } from "./ReferenceVideoCard";
import { ReferencePanel } from "./ReferencePanel";
import { PreprocessingView } from "@/components/canvas/timeline/PreprocessingView";
import { useReferenceVideoStore, referenceVideoCacheKey } from "@/stores/reference-video-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import type { ReferenceResource, ReferenceVideoUnit, TaskStatus } from "@/types";

export interface ReferenceVideoCanvasProps {
  projectName: string;
  episode: number;
  episodeTitle?: string;
}

const EMPTY_UNITS: readonly ReferenceVideoUnit[] = Object.freeze([]);

// 预处理状态小圆点颜色。纯静态映射提到模块顶层，避免每次 render 重建对象。
type PreprocStatus = "loading" | "error" | "empty" | "ready";
const PREPROC_DOT_CLASS: Record<PreprocStatus, string> = {
  loading: "bg-gray-500",
  error: "bg-red-500",
  empty: "bg-gray-500",
  ready: "bg-emerald-500",
};

/** Toast an error with tone="error". Optional `format` wraps the normalized
 *  message (e.g. an i18n template); without it the raw message is shown. */
function toastError(e: unknown, format?: (msg: string) => string): void {
  const msg = errMsg(e);
  useAppStore.getState().pushToast(format ? format(msg) : msg, "error");
}

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
  const loading = useReferenceVideoStore((s) => s.loading);

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

  // #370 optimistic UI：任务队列走 3s 轮询（useTasksSSE.POLL_INTERVAL_MS=3000），
  // 点击按钮到 `relevantTasks` 刷出队列记录之间存在最长 3 秒空窗期。POST 前把
  // unit_id 登记到本地 set，按钮立即显示 busy；`generating` 派生把"optimistic 置位
  // 且队列无对应行"视为真值——happy path 下终态任务在 pageSize 200 窗口里按
  // updated_at DESC 保留，hasQueueRow 持续为 true，set 遗留项永远不再激活。
  // 边界：若任务量或其他类型 churn 把该行挤出 200 条窗口，hasQueueRow 回落到
  // false，遗留项会重新激活，按钮卡在 busy 直到切换 unit 或刷新。对单会话
  // 典型规模（十到数百 unit）可接受；相比显式 pruning，派生逻辑更简单。
  const [optimisticUnitIds, setOptimisticUnitIds] = useState<Set<string>>(() => new Set());

  // #370 任务失败 toast：转变驱动（transition detection），不是状态驱动。
  //
  // 每轮 poll 记下每个 task_id 的上一次 status；只有当**上一轮不是 failed、这一轮
  // 是 failed** 时才算"刚刚发生失败"，冒泡一次 toast。首次看到一个 task 就已经是
  // failed 的（例如进页面时队列里的历史失败记录），prev 为 undefined——被视为我们
  // 没有观测到转变，保持沉默。任务后续轮询里一直是 failed，prev==="failed" 也不再
  // 触发（天然去重，不需要额外 toastedIds set）。
  //
  // 设计抉择：故意不为"3 秒轮询间隔内快速失败"的任务补齐——那种场景 POST 的 info
  // toast（"已加入生成队列"）已经告诉了用户，任务队列 HUD 也会显示状态；拿不到
  // transition 就不 toast，换来"历史失败静默"的正确语义。
  const prevTaskStatusRef = useRef<Map<string, TaskStatus>>(new Map());
  useEffect(() => {
    const prev = prevTaskStatusRef.current;
    const next = new Map<string, TaskStatus>();
    for (const tk of relevantTasks) {
      const before = prev.get(tk.task_id);
      if (tk.status === "failed" && before !== undefined && before !== "failed") {
        useAppStore.getState().pushToast(
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

  // "optimistic 置位 且 队列尚无对应行" OR "队列里就在 queued/running"——
  // 前者覆盖 POST→首次 poll 的 3s 空窗，后者覆盖正常运行期。队列接力后
  // 前半项天然失效，无需显式 pruning。
  const generating = useMemo(() => {
    if (!selected) return false;
    const hasQueueRow = relevantTasks.some((tk) => tk.resource_id === selected.unit_id);
    if (optimisticUnitIds.has(selected.unit_id) && !hasQueueRow) return true;
    return relevantTasks.some(
      (tk) =>
        tk.resource_id === selected.unit_id &&
        (tk.status === "queued" || tk.status === "running"),
    );
  }, [relevantTasks, selected, optimisticUnitIds]);

  const handleAdd = useCallback(async () => {
    try {
      await addUnit(projectName, episode, { prompt: "", references: [] });
    } catch (e) {
      toastError(e);
    }
  }, [addUnit, projectName, episode]);

  const handleGenerate = useCallback(
    async (unitId: string) => {
      setOptimisticUnitIds((s) => {
        if (s.has(unitId)) return s;
        const next = new Set(s);
        next.add(unitId);
        return next;
      });
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
        toastError(e);
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
  // 预处理二级页面：默认 false（主编辑视图）；true 时整个 Canvas 内容替换为 PreprocessingView。
  // 切换 episode 或 project 时都自动退回主视图（切项目而 episode 号相同会复用组件实例，
  // 残留在预处理页会被误解为新项目也在预处理中）——用 render-phase setState 对比而非
  // useEffect，避免 react-hooks/set-state-in-effect lint 规则阻断。
  const [showPreproc, setShowPreproc] = useState(false);
  const [lastEpisode, setLastEpisode] = useState(episode);
  const [lastProject, setLastProject] = useState(projectName);
  if (lastEpisode !== episode || lastProject !== projectName) {
    setLastEpisode(episode);
    setLastProject(projectName);
    setShowPreproc(false);
  }

  // 预处理入口 / 二级页 header 上呈现的状态：loading / error / empty / ready。
  // 用 store.loading + store.error + units.length 综合推导，集中在一处，入口和 header 共用。
  const preprocStatus: PreprocStatus = loading
    ? "loading"
    : error
      ? "error"
      : units.length === 0
        ? "empty"
        : "ready";
  const preprocLabel: Record<PreprocStatus, string> = useMemo(
    () => ({
      loading: t("reference_preproc_status_loading"),
      error: t("reference_preproc_status_error"),
      empty: t("reference_preproc_status_empty"),
      ready: t("reference_units_split_complete", { count: units.length }),
    }),
    [t, units.length],
  );

  // 二级页 header（独占整个 Canvas）：顶部返回按钮一行 + page title 行（左 title / 右 toolbar）。
  // edit/save/cancel toolbar 通过 PreprocessingView 的 renderToolbar slot 抬升到 header 右侧。
  if (showPreproc) {
    return (
      <div className="flex h-full flex-col">
        <div className="border-b border-gray-800 px-4 py-2">
          <button
            type="button"
            onClick={() => setShowPreproc(false)}
            className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200 focus-ring"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            {t("reference_preproc_back")}
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <div className="mx-auto flex max-w-3xl flex-col gap-4 px-6 py-5">
            <PreprocessingView
              projectName={projectName}
              episode={episode}
              contentMode="reference_video"
              compact
              renderToolbar={({ editing, saving, startEdit, save, cancel }) => (
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate text-lg font-semibold text-gray-100">
                      {t("reference_preproc_page_title", { episode })}
                      {episodeTitle ? <span className="text-gray-400">: {episodeTitle}</span> : null}
                    </h2>
                    <p className="mt-1 flex items-center gap-2 text-xs text-gray-500">
                      <span className={`h-1.5 w-1.5 rounded-full ${PREPROC_DOT_CLASS[preprocStatus]}`} aria-hidden="true" />
                      <span>{preprocLabel[preprocStatus]}</span>
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {editing ? (
                      <>
                        <button
                          type="button"
                          onClick={save}
                          disabled={saving}
                          className="inline-flex items-center gap-1 rounded border border-emerald-600/40 bg-emerald-500/10 px-2.5 py-1 text-xs text-emerald-300 transition hover:border-emerald-500 hover:text-emerald-200 disabled:opacity-50 focus-ring"
                        >
                          {saving ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                          ) : (
                            <Save className="h-3.5 w-3.5" aria-hidden="true" />
                          )}
                          {saving ? t("common:saving") : t("common:save")}
                        </button>
                        <button
                          type="button"
                          onClick={cancel}
                          className="inline-flex items-center gap-1 rounded border border-gray-800 bg-gray-900 px-2.5 py-1 text-xs text-gray-400 transition hover:text-gray-200 focus-ring"
                        >
                          <XIcon className="h-3.5 w-3.5" aria-hidden="true" />
                          {t("common:cancel")}
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        onClick={startEdit}
                        className="inline-flex items-center gap-1 rounded border border-gray-800 bg-gray-900 px-2.5 py-1 text-xs text-gray-300 transition hover:border-indigo-500 hover:text-indigo-300 focus-ring"
                      >
                        <Edit3 className="h-3.5 w-3.5" aria-hidden="true" />
                        {t("common:edit")}
                      </button>
                    )}
                  </div>
                </div>
              )}
            />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="@container flex h-full flex-col">
      <div className="px-4 py-3">
        <h2 className="text-lg font-semibold text-gray-100">
          <span translate="no">E{episode}</span>
          {episodeTitle ? `: ${episodeTitle}` : ""}
        </h2>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-500">
          <span>{t("reference_units_count", { count: units.length })}</span>
          <span aria-hidden="true" className="text-gray-700">·</span>
          {/* 预处理入口：inline link 风格而非独立 chip——降低视觉权重的同时把状态色 + 文案 + chevron
              合到副标题行，点击进入二级页面。 */}
          <button
            type="button"
            onClick={() => setShowPreproc(true)}
            className="inline-flex items-center gap-1.5 rounded px-1 py-0.5 text-gray-400 transition-colors hover:text-gray-200 focus-ring"
          >
            {preprocStatus === "loading" ? (
              <Loader2 className="h-3 w-3 animate-spin text-gray-500" aria-hidden="true" />
            ) : (
              <span className={`h-1.5 w-1.5 rounded-full ${PREPROC_DOT_CLASS[preprocStatus]}`} aria-hidden="true" />
            )}
            <span>{preprocLabel[preprocStatus]}</span>
            <ChevronRight className="h-3 w-3" aria-hidden="true" />
          </button>
        </div>
        {error && (
          <p role="alert" className="mt-1 text-xs text-red-400">
            {error}
          </p>
        )}
      </div>
      {/* 外层 grid：<@md(448px) 单列；@md+ 双栏 (UnitList | 右侧 wrapper)。
          断点选 @md 是因为 agent chat 占右半屏时中栏常在 500-700px 区间，@2xl(672px) 错过太多场景。
          单列模式显式两行：UnitList 固 40%（不超过，最少 140px），editor wrapper 拿剩余 1fr——
          否则两个子元素只有 1 个定义行、第二个落进隐式 auto 行，flex-1 链塌到 0，
          textarea 在窄屏完全不可见（#368 后续回归）。@md+ 切回 2 列 × 1 行。 */}
      <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(140px,40%)_minmax(0,1fr)] overflow-hidden @md:grid-cols-[minmax(200px,30%)_1fr] @md:grid-rows-[minmax(0,1fr)]">
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
                <ReferencePanel
                  references={selected.references}
                  projectName={projectName}
                  onReorder={handleReorderRefs}
                  onRemove={handleRemoveRef}
                  onAdd={handleAddRef}
                />
                <div className="flex min-h-0 flex-1 flex-col p-3">
                  <ReferenceVideoCard
                    key={selected.unit_id}
                    unit={selected}
                    projectName={projectName}
                    episode={episode}
                    onChangePrompt={handlePromptChange}
                  />
                </div>
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
