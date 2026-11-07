import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "wouter";

import { useConfirmLeave } from "@/components/shared/edit-unit/LeaveGuard";
import { RetainedEditUnit } from "@/components/shared/edit-unit/RetainedEditUnit";
import { EPISODE_VIEW_PARAM } from "@/app-routes";
import { EditTimelineEmptyState } from "@/components/canvas/edit-render/EditTimelineEmptyState";
import { RenderButton } from "@/components/canvas/edit-render/RenderButton";
import { EpisodeSourceReview } from "@/components/canvas/EpisodeSourceReview";
import { AdScriptHost } from "@/components/canvas/shared/AdScriptDialog";
import { PromptAuthoringHost } from "@/components/canvas/shared/PromptAuthoringDialog";
import { ScriptPlanHost } from "@/components/canvas/shared/ScriptPlanDialog";
import { TextTaskFailureNote } from "@/components/canvas/shared/TextTaskFailureNote";
import { LazyBoundary } from "@/components/shared/LazyBoundary";
import { WorkflowPanel } from "@/components/workflow/WorkflowPanel";
import { DemoEpisodePlaceholder } from "@/onboarding/DemoEpisodePlaceholder";
import { useCostStore } from "@/stores/cost-store";
import { useEpisodeSurfaceRequest } from "@/stores/episode-surface-store";
import { usePromptAuthoringStore } from "@/stores/prompt-authoring-store";
import type { ProjectData } from "@/types";
import type { EpisodeScript } from "@/types/script";
import { gridStoryboardEnabled, normalizeRoute } from "@/utils/generation-mode";
import { lazyNamed } from "@/utils/lazy-component";
import { previewAspect } from "@/utils/preview-aspect";

import {
  episodeViewTabId,
  episodeViewTabs,
  hasEpisodeDraft,
  resolveEpisodeView,
  showsEpisodeSource,
  withEpisodeView,
  type CanvasView,
  type EpisodeView,
  type EpisodeViewFacts,
} from "./episode-view";
import { EpisodeHead } from "./EpisodeHead";
import { EpisodeHeaderActions, EpisodeHeaderSlotProvider } from "./EpisodeHeaderActions";
import { EpisodePageHeader } from "./EpisodePageHeader";
import { EpisodeViewFactsProvider } from "./EpisodeViewScope";
import { StorySettingView } from "./StorySettingView";

// Editing timeline player/tracks are used only in editing view; load on demand.
const EditTimelineView = lazyNamed(() => import("@/components/canvas/edit/EditTimelineView"), "EditTimelineView");

export interface EpisodeViewChangeOptions {
  /** Replace history instead of pushing: programmatic navigation such as unit jumps should not add a back step. */
  replace?: boolean;
}

export interface EpisodeCanvasContext {
  view: CanvasView;
  onViewChange: (view: EpisodeView, options?: EpisodeViewChangeOptions) => void;
}

/**
 * Episode page: two header rows (episode head, progress, view tabs/action slots), then the view region.
 *
 * URL `?view=` stores views (see `episode-view.ts`). Script planning/grids/storyboards share one canvas,
 * retained across switches. Canvas editing units mount only in certain views; `EpisodeViewFactsProvider` determines unmounting navigation.
 * Story settings (ads only) and editing replace the whole canvas. Before script generation, planning shows source confirmation.
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
  /** Formal episode script, null before it exists. */
  script: EpisodeScript | null;
  /** Demo projects: read-only, without production progress/editing view. */
  demo: boolean;
  /** Save episode title; rejection preserves title editing. */
  onSaveTitle: (title: string) => Promise<void>;
  /** View a unit from production progress. */
  onViewUnit: (unitId: string) => void;
  /** Regenerate a specified step's units from production progress; omitted means view-only. */
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

  // Unavailable view switches do nothing; selecting the current view writes no history.
  const changeView = useCallback(
    (next: EpisodeView, options?: EpisodeViewChangeOptions) => {
      if (next === view || !tabs.some((tab) => tab.view === next && !tab.disabled)) return;
      setSearchParams((params) => withEpisodeView(params, next, facts), { replace: options?.replace });
    },
    [view, tabs, facts, setSearchParams],
  );

  // Progress view actions select shots/units only in storyboard view. When elsewhere, combine view switch/location into one leave guard;
  // issue positioning only after approval. Continuing editing changes nothing and leaves no pending positioning for later view mounting.
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

  // Header estimated costs use cost estimates; demo cost tables clear themselves.
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
      <LazyBoundary variant="pane">
        <EditTimelineView
          key={`${projectName}::${episode}`}
          projectName={projectName}
          episode={episode}
          script={script}
          aspect={previewAspect(projectData)}
          ttsNarration={ttsNarration}
          renderActions={({ timelineId, timelineName, issues, showIssues }) =>
            // Before reads complete, blocking-issue presence is unknown; omit render entry.
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
      </LazyBoundary>
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
            // Reset on episode changes: partial title drafts/deletion confirmations must not carry across episodes.
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
            // Demo mode has no real project facts to project; do not mount production progress.
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

/** Retain the visible episode page: asynchronous script arrival during source editing must not unmount editors. */
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
