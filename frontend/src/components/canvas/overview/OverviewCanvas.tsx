import { useCallback, useEffect, useRef, useState } from "react";

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
  /** 只读展示（引导演示项目）：不渲染编辑与生成入口。 */
  readOnly?: boolean;
}

function hasStorySetting(overview: ProjectOverview | undefined): boolean {
  return Boolean(overview?.synopsis || overview?.genre || overview?.theme || overview?.world_setting);
}

/**
 * 项目概览：单列限宽的设定页（页头、资产完成度、费用、故事设定）；空项目显示欢迎页。
 * 从欢迎页上传整本原文后立即切到概览，故事设定区显示骨架，生成完成后就地填入。
 * 广告项目没有初始化页：概览常驻「创作灵感」与「商品」区承担首次录入，故事设定在视频页的「故事设定」tab。
 */
export function OverviewCanvas({ projectName, projectData, readOnly = false }: OverviewCanvasProps) {
  const isAd = projectData?.content_mode === "ad";
  const debouncedFetch = useCostStore((s) => s.debouncedFetch);

  useEffect(() => {
    // 演示项目的费用请求交给费用 store 自身的 isDemoProject 分支跳过并失效：
    // 那条分支会取消已排队的防抖计时器、abort 在途的真实项目请求、清空费用状态。
    if (!projectName) return;
    debouncedFetch(projectName);
  }, [projectName, projectData?.episodes, debouncedFetch]);

  // 上传对话框按项目记录，切项目后不沿用。对话框打开期间保留欢迎页（见 showWelcome），
  // 上传登记出整本源文或第一集后背景不会先闪成概览。
  const [upload, setUpload] = useState<{ projectName: string; files: File[] } | null>(null);
  const uploadFiles = upload?.projectName === projectName ? upload.files : null;

  // 从原文生成故事设定：首次上传后自动开始，或由「从原文生成」触发。状态在 store 里按项目记录，
  // 生成途中离开概览再回来仍显示读取中，完成后就地填入。
  const generating = useOverviewGenerateStore((s) => Boolean(s.generating[projectName]));
  const generateError = useOverviewGenerateStore((s) => s.errors[projectName] ?? null);
  const generate = useOverviewGenerateStore((s) => s.generate);
  const runGenerate = useCallback(() => void generate(projectName), [generate, projectName]);

  const handleUploaded = useCallback(
    (result: SourceUploadResult) => {
      // 第一次放进整本源文时就地读取原文；只登记了逐集原文时不生成
      if (result.wholeSourceFiles.length > 0) runGenerate();
    },
    [runGenerate],
  );

  // 交接提示：本次会话内故事设定由空变为有内容时触发一次；只读态与广告项目不触发。
  // 按项目记录上一次看到的状态，切项目时不把上一个项目的状态当成这个项目的变化。
  const lastSeenRef = useRef<{ projectName: string; empty: boolean } | null>(null);
  useEffect(() => {
    if (readOnly || isAd) {
      lastSeenRef.current = null;
      return;
    }
    if (!projectData) return;
    const empty = !hasStorySetting(projectData.overview);
    const last = lastSeenRef.current;
    if (last?.projectName === projectName && last.empty && !empty) useHandoffTipStore.getState().trigger(projectName);
    lastSeenRef.current = { projectName, empty };
  }, [projectData, projectName, readOnly, isAd]);

  if (!projectData) {
    // 项目数据加载期间保留空容器，避免「居中提示 → 顶端内容」的位置跳动
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
