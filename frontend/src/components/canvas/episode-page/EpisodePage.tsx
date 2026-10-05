import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "wouter";

import { useConfirmLeave } from "@/components/shared/edit-unit/LeaveGuard";
import { RetainedEditUnit } from "@/components/shared/edit-unit/RetainedEditUnit";
import { EPISODE_VIEW_PARAM } from "@/app-routes";
import { EditTimelineView } from "@/components/canvas/edit/EditTimelineView";
import { EditTimelineEmptyState } from "@/components/canvas/edit-render/EditTimelineEmptyState";
import { RenderButton } from "@/components/canvas/edit-render/RenderButton";
import { EpisodeSourceReview } from "@/components/canvas/EpisodeSourceReview";
import { AdScriptHost } from "@/components/canvas/shared/AdScriptDialog";
import { PromptAuthoringHost } from "@/components/canvas/shared/PromptAuthoringDialog";
import { ScriptPlanHost } from "@/components/canvas/shared/ScriptPlanDialog";
import { TextTaskFailureNote } from "@/components/canvas/shared/TextTaskFailureNote";
import { WorkflowPanel } from "@/components/workflow/WorkflowPanel";
import { DemoEpisodePlaceholder } from "@/onboarding/DemoEpisodePlaceholder";
import { useCostStore } from "@/stores/cost-store";
import { useEpisodeSurfaceRequest } from "@/stores/episode-surface-store";
import { usePromptAuthoringStore } from "@/stores/prompt-authoring-store";
import type { EpisodeMeta, ProjectData } from "@/types";
import type { EpisodeScript } from "@/types/script";
import { gridStoryboardEnabled, normalizeRoute } from "@/utils/generation-mode";
import { previewAspect } from "@/utils/preview-aspect";

import {
  episodeViewTabs,
  resolveEpisodeView,
  withEpisodeView,
  type CanvasView,
  type EpisodeView,
  type EpisodeViewFacts,
} from "./episode-view";
import { EpisodeHead } from "./EpisodeHead";
import { EpisodeHeaderActions, EpisodeHeaderSlotProvider } from "./EpisodeHeaderActions";
import { EpisodePageHeader, episodeViewTabId } from "./EpisodePageHeader";
import { EpisodeViewFactsProvider } from "./EpisodeViewScope";
import { StorySettingView } from "./StorySettingView";

export interface EpisodeViewChangeOptions {
  /** 替换当前历史记录而不是新增一条：程序触发的切换（如跳到某个单元）用它，后退不必多退一步。 */
  replace?: boolean;
}

export interface EpisodeCanvasContext {
  view: CanvasView;
  onViewChange: (view: EpisodeView, options?: EpisodeViewChangeOptions) => void;
}

/** 本集已有中间稿：分段稿或生成好的剧本草稿。 */
function hasEpisodeDraft(meta: EpisodeMeta | undefined): boolean {
  return meta?.script_status === "segmented" || meta?.script_status === "generated";
}

/**
 * 脚本规划视图呈现集原文：已选集，但正式剧本与中间稿都没有。
 * 广告/短片恒单集、演示项目没有源文可切，走各自画布。
 */
export function showsEpisodeSource(
  projectData: ProjectData | null,
  episode: number,
  script: EpisodeScript | null,
  demo: boolean,
): boolean {
  const meta = projectData?.episodes?.find((entry) => entry.episode === episode);
  return meta !== undefined && !script && !hasEpisodeDraft(meta) && projectData?.content_mode !== "ad" && !demo;
}

/**
 * 一集的页面：两行页头（集头、制作进度、视图 tab 与动作插槽）与下方的视图区。
 *
 * 视图记在地址的 `?view=` 上（见 `episode-view.ts`）。脚本规划、多宫格分镜图与分镜三个视图由同一个画布承担，
 * 切换时画布不卸载；画布里的编辑单元只挂在某一个视图下，经 `EpisodeViewFactsProvider` 判断哪些跳转会卸载自己。
 * 故事设定（只在广告项目出现）与剪辑视图替换整个画布。剧本还没生成时脚本规划视图是集原文确认。
 */
function EpisodePageContent({
  projectName,
  episode,
  projectData,
  script,
  demo,
  onSaveTitle,
  onViewUnit,
  onRegenerate,
  renderCanvas,
}: {
  projectName: string;
  episode: number;
  projectData: ProjectData | null;
  /** 本集的正式剧本；还没有时为 null。 */
  script: EpisodeScript | null;
  /** 演示项目：只读，没有制作进度与剪辑视图。 */
  demo: boolean;
  /** 保存集标题；reject 时标题保持编辑态。 */
  onSaveTitle: (title: string) => Promise<void>;
  /** 制作进度里「查看」某个单元。 */
  onViewUnit: (unitId: string) => void;
  /** 制作进度里重生某一步的指定单元；不传时只给「查看」。 */
  onRegenerate?: (stepId: string, unitIds: string[]) => void;
  renderCanvas: (context: EpisodeCanvasContext) => ReactNode;
}) {
  const { t } = useTranslation("dashboard");
  const [searchParams, setSearchParams] = useSearchParams();
  const [actionsSlot, setActionsSlot] = useState<HTMLDivElement | null>(null);

  const meta = projectData?.episodes?.find((entry) => entry.episode === episode);
  const isAd = projectData?.content_mode === "ad";
  const route = normalizeRoute(projectData?.generation_mode);
  const hasScript = Boolean(script);
  const hasDraft = hasEpisodeDraft(meta);
  const grid = gridStoryboardEnabled(projectData);
  const sourceReview = showsEpisodeSource(projectData, episode, script, demo);
  const facts = useMemo<EpisodeViewFacts>(
    () => ({ isAd, route, grid, hasScript, hasDraft, sourceReview, demo }),
    [isAd, route, grid, hasScript, hasDraft, sourceReview, demo],
  );
  const tabs = useMemo(() => episodeViewTabs(facts), [facts]);
  const view = resolveEpisodeView(searchParams.get(EPISODE_VIEW_PARAM), facts);

  // 切到不可选的视图不生效；切到当前视图不写历史。
  const changeView = useCallback(
    (next: EpisodeView, options?: EpisodeViewChangeOptions) => {
      if (next === view || !tabs.some((tab) => tab.view === next && !tab.disabled)) return;
      setSearchParams((params) => withEpisodeView(params, next, facts), { replace: options?.replace });
    },
    [view, tabs, facts, setSearchParams],
  );

  // 制作进度里的「查看」：分镜与视频单元只在分镜视图里选得中。不在该视图时，切视图与定位合成一次离开拦截，
  // 放行后才发出定位请求；选择继续编辑时什么都不变，也不留下等分镜视图挂载后才消费的定位。
  const confirmLeave = useConfirmLeave();
  const viewUnit = useCallback(
    (unitId: string) => {
      if (view === "board") {
        onViewUnit(unitId);
        return;
      }
      confirmLeave(() => {
        changeView("board", { replace: true });
        onViewUnit(unitId);
      });
    },
    [view, changeView, onViewUnit, confirmLeave],
  );

  useEpisodeSurfaceRequest(projectName, episode, "script_plan", () => changeView("plan"));
  useEpisodeSurfaceRequest(projectName, episode, "prompt_authoring_draft", () => changeView("board"));

  // 页头的预估费用读费用估算；演示项目由费用表自己清空。
  const debouncedFetch = useCostStore((s) => s.debouncedFetch);
  useEffect(() => {
    debouncedFetch(projectName);
  }, [projectName, episode, debouncedFetch]);

  const openAuthorPrompts = useCallback(
    () => usePromptAuthoringStore.getState().open({ projectName, episode, scope: "pending" }),
    [projectName, episode],
  );

  const ttsNarration = projectData?.narration_delivery === "use_tts";
  let body: ReactNode;
  if (view === "edit") {
    body = (
      <EditTimelineView
        key={`${projectName}::${episode}`}
        projectName={projectName}
        episode={episode}
        script={script}
        aspect={previewAspect(projectData)}
        ttsNarration={ttsNarration}
        renderActions={({ timelineId, timelineName, issues, showIssues }) =>
          // 读取完成前不知道有没有阻断级 issue，先不给出片入口。
          issues === null ? null : (
            <EpisodeHeaderActions>
              <RenderButton
                projectName={projectName}
                timelineId={timelineId}
                timelineName={timelineName}
                issues={issues}
                narrationAvailable={ttsNarration}
                onShowIssues={showIssues}
              />
            </EpisodeHeaderActions>
          )
        }
        renderEmptyState={({ reload }) => (
          <EditTimelineEmptyState projectName={projectName} episode={episode} onCreated={reload} />
        )}
      />
    );
  } else if (view === "setting") {
    body = (
      <EpisodeViewFactsProvider value={facts}>
        <StorySettingView projectName={projectName} overview={projectData?.overview} readOnly={demo} />
      </EpisodeViewFactsProvider>
    );
  } else if (demo && !script) {
    body = <DemoEpisodePlaceholder />;
  } else if (sourceReview) {
    body = <EpisodeViewFactsProvider value={facts}><EpisodeSourceReview projectName={projectName} episode={episode} episodes={projectData?.episodes ?? []} /></EpisodeViewFactsProvider>;
  } else {
    body = <EpisodeViewFactsProvider value={facts}>{renderCanvas({ view, onViewChange: changeView })}</EpisodeViewFactsProvider>;
  }

  return (
    <EpisodeHeaderSlotProvider value={actionsSlot}>
      <div className="flex min-h-0 flex-1 flex-col">
        <EpisodePageHeader
          head={
            // 换集时重置：编辑到一半的标题草稿与删除确认不能带到另一集
            <EpisodeHead
              key={`${projectName}::${episode}`}
              projectName={projectName}
              episode={episode}
              meta={meta}
              route={route}
              canEditTitle={Boolean(meta) && !demo}
              onSaveTitle={onSaveTitle}
              canDelete={!isAd && !demo}
            />
          }
          progress={
            // 演示态没有真实项目事实可投影，制作进度不挂载。
            demo ? null : (
              <WorkflowPanel
                projectName={projectName}
                episode={episode}
                onViewUnit={viewUnit}
                onRegenerate={onRegenerate}
                onAuthorPrompts={script ? openAuthorPrompts : undefined}
              />
            )
          }
          tabs={tabs}
          view={view}
          onViewChange={changeView}
          boardLabel={t(route === "reference_video" ? "episode_view_units" : "episode_view_board")}
          onActionsSlot={setActionsSlot}
        />
        {!demo && (
          <>
            <TextTaskFailureNote projectName={projectName} episode={episode} isAd={isAd} hasScript={hasScript} />
            <ScriptPlanHost
              projectName={projectName}
              episode={episode}
              savedInstructions={meta?.script_plan_instructions}
            />
            {isAd && <AdScriptHost projectName={projectName} episode={episode} />}
            <PromptAuthoringHost
              projectName={projectName}
              episode={episode}
              script={script}
              savedInstructions={meta?.prompt_authoring_instructions}
            />
          </>
        )}
        <div role="tabpanel" aria-labelledby={episodeViewTabId(view)} className="flex min-h-0 flex-1 flex-col">
          {body}
        </div>
      </div>
    </EpisodeHeaderSlotProvider>
  );
}

/** 保留的是可见集页；原文编辑期间脚本到达，不让异步事件卸载编辑器。 */
export function EpisodePage(props: Parameters<typeof EpisodePageContent>[0]) {
  const { t } = useTranslation("dashboard");
  const meta = props.projectData?.episodes?.find((entry) => entry.episode === props.episode);
  const source = showsEpisodeSource(props.projectData, props.episode, props.script, props.demo);
  const identity = `${props.projectName}:${props.episode}:${!meta ? "missing" : source ? "source" : props.script ? "script" : "draft"}`;
  const message = !meta ? "episode_externally_removed" : props.script || hasEpisodeDraft(meta) ? "episode_source_replaced" : "episode_script_removed";
  return (
    <RetainedEditUnit identity={identity} value={props} message={t(message)}>
      {(shown) => <EpisodePageContent {...shown} />}
    </RetainedEditUnit>
  );
}
