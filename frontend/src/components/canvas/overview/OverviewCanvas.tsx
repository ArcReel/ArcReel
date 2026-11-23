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
  readOnly?: boolean;
}

function hasStorySetting(overview: ProjectOverview | undefined): boolean {
  return Boolean(overview?.synopsis || overview?.genre || overview?.theme || overview?.world_setting);
}

export function OverviewCanvas({ projectName, projectData, readOnly = false }: OverviewCanvasProps) {
  const isAd = projectData?.content_mode === "ad";
  const debouncedFetch = useCostStore((s) => s.debouncedFetch);

  useEffect(() => {
    if (!projectName) return;
    debouncedFetch(projectName);
  }, [projectName, projectData?.episodes, debouncedFetch]);

  const [upload, setUpload] = useState<{ projectName: string; files: File[] } | null>(null);
  const uploadFiles = upload?.projectName === projectName ? upload.files : null;

  const generating = useOverviewGenerateStore((s) => Boolean(s.generating[projectName]));
  const generateError = useOverviewGenerateStore((s) => s.errors[projectName] ?? null);
  const generate = useOverviewGenerateStore((s) => s.generate);
  const runGenerate = useCallback(() => void generate(projectName), [generate, projectName]);

  const handleUploaded = useCallback(
    (result: SourceUploadResult) => {
      if (result.wholeSourceFiles.length > 0) runGenerate();
    },
    [runGenerate],
  );

  const observeStorySetting = useHandoffTipStore((s) => s.observe);
  useEffect(() => {
    if (readOnly || isAd || !projectData) return;
    observeStorySetting(projectName, !hasStorySetting(projectData.overview));
  }, [projectData, projectName, readOnly, isAd, observeStorySetting]);

  if (!projectData) {
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
