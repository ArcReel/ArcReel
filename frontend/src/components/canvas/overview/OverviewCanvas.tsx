import { useCallback, useEffect, useRef, useState } from "react";

import { API } from "@/api";
import { AdBriefCard } from "@/components/canvas/AdBriefCard";
import { AdInitCanvas } from "@/components/canvas/AdInitCanvas";
import { SourceUploadDialog, type SourceUploadResult } from "@/components/canvas/episodes/SourceUploadDialog";
import { AgentHandoffHint } from "@/components/copilot/AgentHandoffHint";
import { useCostStore } from "@/stores/cost-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData, ProjectOverview } from "@/types";
import { errMsg } from "@/utils/async";
import { outputTruncationOfError } from "@/utils/output-truncation";

import { AssetProgressLine } from "./AssetProgressLine";
import { CostLine } from "./CostLine";
import { OverviewHeader } from "./OverviewHeader";
import { StorySetting, type StoryGenerateError } from "./StorySetting";
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

  // 从原文生成故事设定：首次上传后自动开始，或由「从原文生成」触发。按项目记录，切项目时作废。
  const [generatingFor, setGeneratingFor] = useState<string | null>(null);
  const [generateError, setGenerateError] = useState<{ projectName: string; error: StoryGenerateError } | null>(null);
  const generating = generatingFor === projectName;
  const generateControllerRef = useRef<AbortController | null>(null);
  useEffect(() => () => generateControllerRef.current?.abort(), [projectName]);

  const runGenerate = useCallback(async () => {
    generateControllerRef.current?.abort();
    const controller = new AbortController();
    generateControllerRef.current = controller;
    setGeneratingFor(projectName);
    setGenerateError(null);
    try {
      await API.generateOverview(projectName, { signal: controller.signal });
      if (controller.signal.aborted) return;
      // refreshProject 以结算值报告失败而不 reject：生成已落盘却停在旧内容上，会引人再生成一次
      const refreshed = await useProjectsStore.getState().refreshProject(projectName);
      if (refreshed === "failed" && !controller.signal.aborted) {
        setGenerateError({ projectName, error: { kind: "refresh" } });
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      setGenerateError({
        projectName,
        error: { kind: "generate", message: errMsg(err), truncation: outputTruncationOfError(err) },
      });
    } finally {
      if (generateControllerRef.current === controller) {
        generateControllerRef.current = null;
        setGeneratingFor(null);
      }
    }
  }, [projectName]);

  const handleUploaded = useCallback(
    (result: SourceUploadResult) => {
      // 第一次放进整本源文时就地读取原文；只登记了逐集原文时不生成
      if (result.wholeSourceFiles.length > 0) void runGenerate();
    },
    [runGenerate],
  );

  // Agent 交接提示：本次会话内故事设定由空变为有内容时触发一次；只读态与广告项目不触发。
  // 切项目时 trigger 归零，避免提示按 `<项目>:<trigger>` 去重时把上一个项目的计数当成新事件。
  const [handoffTrigger, setHandoffTrigger] = useState(0);
  const lastSeenRef = useRef<{ projectName: string; empty: boolean } | null>(null);
  useEffect(() => {
    if (readOnly || isAd) {
      lastSeenRef.current = null;
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 切入只读态或广告项目时清零交接提示的 trigger，是有意的状态重置
      setHandoffTrigger(0);
      return;
    }
    if (!projectData) return;
    const empty = !hasStorySetting(projectData.overview);
    const last = lastSeenRef.current;
    if (last && last.projectName !== projectName) setHandoffTrigger(0);
    else if (last?.empty && !empty) setHandoffTrigger((k) => k + 1);
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
  // 广告项目在没有商品与创作灵感时进入初始化页
  const showAdInit =
    isAd && !readOnly && Object.keys(projectData.products ?? {}).length === 0 && !(projectData.brief ?? "").trim();

  const refreshProject = async () => {
    await useProjectsStore.getState().refreshProject(projectName);
  };

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
          {showAdInit ? (
            <AdInitCanvas projectName={projectName} onDone={refreshProject} />
          ) : (
            <>
              {isAd ? (
                <AdBriefCard
                  projectName={projectName}
                  brief={projectData.brief ?? ""}
                  targetDuration={projectData.target_duration}
                  readOnly={readOnly}
                  onSaved={refreshProject}
                />
              ) : null}
              <StorySetting
                key={projectName}
                projectName={projectName}
                overview={projectData.overview}
                readOnly={readOnly}
                canGenerate={hasWholeSource || hasStorySetting(projectData.overview)}
                generating={generating}
                generateError={generateError?.projectName === projectName ? generateError.error : null}
                onGenerate={() => void runGenerate()}
              />
            </>
          )}
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
      {!readOnly ? <AgentHandoffHint triggerKey={handoffTrigger} storageScope={projectName} /> : null}
    </div>
  );
}
