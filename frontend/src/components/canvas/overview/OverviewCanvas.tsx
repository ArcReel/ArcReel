import { useCallback, useEffect, useState } from "react";

import { SourceUploadDialog, type SourceUploadResult } from "@/components/canvas/episodes/SourceUploadDialog";
import { useCostStore } from "@/stores/cost-store";
import { useOverviewGenerateStore } from "@/stores/overview-generate-store";
import type { ProjectData, ProjectOverview } from "@/types";

import { AdBrief } from "./AdBrief";
import { AdProducts } from "./AdProducts";
import { AssetProgressLine } from "./AssetProgressLine";
import { CostLine } from "./CostLine";
import { HandoffTip } from "./HandoffTip";
import { OverviewHeader } from "./OverviewHeader";
import { StorySetting } from "./StorySetting";
import { useHandoffTipStore } from "./useHandoffTip";
import { WelcomeCanvas } from "./WelcomeCanvas";

interface OverviewCanvasProps {
  projectName: string;
  projectData: ProjectData | null;
  /** Read-only onboarding demos omit editing/generation entry points. */
  readOnly?: boolean;
}

function hasStorySetting(overview: ProjectOverview | undefined): boolean {
  return Boolean(overview?.synopsis || overview?.genre || overview?.theme || overview?.world_setting);
}

/**
 * Project overview: width-limited single-column settings (header, asset completion, costs, story settings), welcome for empty projects.
 * Whole-source welcome uploads immediately switch to overview with story-setting skeletons, filled in place on completion.
 * Ads have no initialization page: persistent inspiration/products sections handle initial input; story settings live on the video-page story-settings tab.
 */
export function OverviewCanvas({ projectName, projectData, readOnly = false }: OverviewCanvasProps) {
  const isAd = projectData?.content_mode === "ad";
  const debouncedFetch = useCostStore((s) => s.debouncedFetch);

  useEffect(() => {
    // Cost stores' isDemoProject branch skips/invalidates demo requests itself:
    // it cancels queued debounce timers, aborts real-project requests, and clears cost state.
    if (!projectName) return;
    debouncedFetch(projectName);
  }, [projectName, projectData?.episodes, debouncedFetch]);

  // Upload-dialog state is project-scoped, never reused across projects. Retain welcome while open (showWelcome),
  // avoiding background flashes to overview once whole-source/first-episode registration lands.
  const [upload, setUpload] = useState<{ projectName: string; files: File[] } | null>(null);
  const uploadFiles = upload?.projectName === projectName ? upload.files : null;

  // Generate story settings from source automatically after initial upload or through generate-from-source; state is stored per project,
  // so leaving/returning during generation still shows reading and fills results in place.
  const generating = useOverviewGenerateStore((s) => Boolean(s.generating[projectName]));
  const generateError = useOverviewGenerateStore((s) => s.errors[projectName] ?? null);
  const generate = useOverviewGenerateStore((s) => s.generate);
  const runGenerate = useCallback(() => void generate(projectName), [generate, projectName]);

  const handleUploaded = useCallback(
    (result: SourceUploadResult) => {
      // First whole-source insertion reads sources in place; registering only episode sources does not generate.
      if (result.wholeSourceFiles.length > 0) runGenerate();
    },
    [runGenerate],
  );

  // Handoff tip triggers once per session when story settings change empty→populated; excludes read-only/ads.
  // Store prior observed states per project, never interpreting previous-project state as current-project changes;
  // background-completed story settings during absence count as changes on return too.
  const observeStorySetting = useHandoffTipStore((s) => s.observe);
  useEffect(() => {
    if (readOnly || isAd || !projectData) return;
    observeStorySetting(projectName, !hasStorySetting(projectData.overview));
  }, [projectData, projectName, readOnly, isAd, observeStorySetting]);

  if (!projectData) {
    // Retain empty containers while project data loads, avoiding centered-hint to top-content position shifts.
    return <div className="relative flex min-h-0 flex-1" aria-busy="true" />;
  }

  const hasWholeSource = (projectData.whole_source_files?.length ?? 0) > 0;
  const emptyProject =
    !isAd && !hasStorySetting(projectData.overview) && (projectData.episodes?.length ?? 0) === 0 && !hasWholeSource;
  const showWelcome = !readOnly && !generating && (emptyProject || uploadFiles !== null);

  return (
    <div className="relative flex min-h-0 flex-1 flex-col overflow-y-auto p-6 [scrollbar-gutter:stable] @3xl/canvas:p-8">
      {showWelcome ? (
        <WelcomeCanvas
          projectTitle={projectData.title}
          onSelectFiles={(files) => setUpload({ projectName, files })}
        />
      ) : (
        <div className="flex w-full max-w-190 shrink-0 flex-col gap-6">
          <div className="flex flex-col gap-3">
            <OverviewHeader projectName={projectName} data={projectData} readOnly={readOnly} />
            <div className="flex flex-col gap-1.5">
              <AssetProgressLine data={projectData} />
              <CostLine projectName={projectName} readOnly={readOnly} />
            </div>
          </div>
          {isAd ? (
            <>
              <AdBrief
                key={projectName}
                projectName={projectName}
                brief={projectData.brief}
                targetDuration={projectData.target_duration}
                readOnly={readOnly}
              />
              <AdProducts
                key={`${projectName}:products`}
                projectName={projectName}
                products={projectData.products}
                readOnly={readOnly}
              />
            </>
          ) : (
            <StorySetting
              key={projectName}
              projectName={projectName}
              overview={projectData.overview}
              readOnly={readOnly}
              canGenerate={hasWholeSource || hasStorySetting(projectData.overview)}
              generating={generating}
              generateError={generateError}
              onGenerate={runGenerate}
            />
          )}
          {!isAd && !readOnly ? <HandoffTip projectName={projectName} /> : null}
        </div>
      )}
      {uploadFiles !== null ? (
        <SourceUploadDialog
          projectName={projectName}
          initialFiles={uploadFiles}
          onClose={() => setUpload(null)}
          onUploaded={handleUploaded}
        />
      ) : null}
    </div>
  );
}
