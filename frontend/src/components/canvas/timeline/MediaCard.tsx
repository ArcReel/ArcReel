import { useRef, useState } from "react";
import { isResourceBusy } from "@/stores/tasks-store";
import { Sparkles, ImageIcon, Film } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { usePlaybackStart } from "@/hooks/usePlaybackStart";
import { useProjectsStore } from "@/stores/projects-store";
import { AspectFrame } from "@/components/canvas/shared/AspectFrame";
import { PreviewableImageFrame } from "@/components/canvas/shared/PreviewableImageFrame";
import { PresentationPlayer } from "@/components/shared/PresentationPlayer";
import {
  UPLOAD_IMAGE_ACCEPT,
  UPLOAD_VIDEO_ACCEPT,
  UploadIconButton,
} from "@/components/canvas/shared/UploadIconButton";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { formatCost } from "@/utils/cost-format";
import type { CostBreakdown } from "@/types";
import { itemIdWithinEpisode } from "@/utils/episode-display";
import { ImageEditButton } from "./ImageEditButton";
import { VersionTimeMachine } from "./VersionTimeMachine";

type MediaKind = "storyboard" | "video";

interface MediaCardProps {
  kind: MediaKind;
  restoring?: boolean;
  onRestoringChange?: (restoring: boolean) => void;
  checkBusy?: () => boolean;
  projectName: string;
  segmentId: string;
  /** 资产相对路径，如 storyboards/E1S2_v1.png */
  assetPath: string | null;
  /** 视频海报缩略图（仅 kind=video 用） */
  posterPath?: string | null;
  /** 渲染比例 */
  aspectRatio: "9:16" | "16:9";
  /** 生成按钮是否禁用（视频生成需要先有分镜图） */
  generateDisabled?: boolean;
  /** 自定义禁用 tooltip，未提供时使用默认（"分镜图未生成"）的视频禁用提示 */
  generateDisabledHint?: string;
  /** 进行中状态 */
  generating?: boolean;
  /** 估算费用（按币种 breakdown，例如 {USD: 0.12} 或 {CNY: 5.25}） */
  estimatedCost?: CostBreakdown;
  /** 触发生成 */
  onGenerate?: () => void;
  /** 生成按钮文案，缺省按有无产物写「生成」或「重新生成」；有未保存修改时传「保存并生成」。 */
  generateLabel?: string;
  /** 版本恢复回调；未提供时不显示版本入口（只读展示无版本可回滚） */
  onRestore?: () => Promise<unknown> | void;
  /** 自主上传回调（替换该分镜的分镜图/视频）；未提供时不显示上传入口 */
  onUpload?: (file: File) => Promise<void> | void;
  /** 本卡片的上传请求进行中 */
  uploading?: boolean;
  /** 其他上传进行中等需要互斥的场景：禁用上传入口但不显示 spinner */
  uploadDisabled?: boolean;
  /** 分镜编辑所需的剧集文件；提供时（且 kind=storyboard、已有图）显示编辑入口 */
  editScriptFile?: string | null;
}

const UPLOAD_ACCEPT: Record<MediaKind, string> = {
  storyboard: UPLOAD_IMAGE_ACCEPT,
  video: UPLOAD_VIDEO_ACCEPT,
};

export function MediaCard({
  kind,
  projectName,
  segmentId,
  assetPath,
  posterPath,
  aspectRatio,
  generateDisabled,
  generateDisabledHint,
  generating,
  estimatedCost,
  onGenerate,
  generateLabel: generateLabelOverride,
  onRestore,
  onUpload,
  uploading,
  uploadDisabled,
  editScriptFile,
  restoring: externalRestoring,
  onRestoringChange,
  checkBusy,
}: MediaCardProps) {
  const { t } = useTranslation("dashboard");
  const [restoring, setRestoring] = useState(false);
  const restoringRef = useRef(false);
  const freshBusy = () => restoringRef.current || Boolean(checkBusy?.()) || isResourceBusy(kind, projectName, segmentId);
  const setRestoreBusy = (next: boolean) => {
    restoringRef.current = next;
    setRestoring(next);
    onRestoringChange?.(next);
  };
  // 演示态只读：卡片上的四个写入口（上传 / 编辑 / 版本恢复 / 生成）从同一处判定关闭，
  // 不再各自靠「对应回调是否传入」推断——那让版本入口与其余入口分属两套机制。
  const demoReadOnly = useDemoWorkbench();

  const assetFp = useProjectsStore((s) =>
    assetPath ? s.getAssetFingerprint(assetPath) : null,
  );
  const playbackStart = usePlaybackStart("videos", segmentId);
  const assetUrl = assetPath ? API.getFileUrl(projectName, assetPath, assetFp) : null;

  const Icon = kind === "storyboard" ? ImageIcon : Film;
  const title =
    kind === "storyboard" ? t("media_storyboard_title") : t("media_video_title");
  const generateLabel =
    generateLabelOverride ??
    (kind === "storyboard"
      ? assetPath
        ? t("media_regenerate_storyboard")
        : t("media_generate_storyboard")
      : assetPath
        ? t("media_regenerate_video")
        : t("media_generate_video"));
  const resourceType: "storyboards" | "videos" =
    kind === "storyboard" ? "storyboards" : "videos";
  // uploadDisabled 是本卡片之外的互斥占用（如同一分镜另一张卡在上传中）；
  // 编辑/版本恢复/生成同样写这个资源，须一并禁用，否则会与占用中的写操作并发冲突。
  const resourceBusy = generating || uploading || uploadDisabled || restoring || externalRestoring;

  return (
    <div>
      <div className="mb-2 flex min-h-7 items-center gap-1.5">
        <Icon aria-hidden className="size-3.5 text-muted-foreground" />
        <h3 className="text-xs font-medium text-subtle-foreground">{title}</h3>
        <span className="flex-1" />
        {onUpload && !demoReadOnly && (
          <UploadIconButton
            accept={UPLOAD_ACCEPT[kind]}
            label={
              kind === "storyboard"
                ? t("media_upload_storyboard")
                : t("media_upload_video")
            }
            busy={uploading}
            disabled={resourceBusy}
            onSelect={(f) => { if (!freshBusy()) void onUpload(f); }}
          />
        )}
        {kind === "storyboard" && editScriptFile && !demoReadOnly && (
          <ImageEditButton
            projectName={projectName}
            resourceType="storyboard"
            resourceId={segmentId}
            scriptFile={editScriptFile}
            hasImage={Boolean(assetPath)}
            busy={resourceBusy}
          />
        )}
        {onRestore && !demoReadOnly && (
          <VersionTimeMachine
            projectName={projectName}
            resourceType={resourceType}
            resourceId={segmentId}
            onRestore={onRestore}
            busy={resourceBusy}
            checkBusy={freshBusy}
            onRestoringChange={setRestoreBusy}
          />
        )}
      </div>

      {assetUrl ? (
        kind === "storyboard" ? (
          <PreviewableImageFrame src={assetUrl} alt={`${itemIdWithinEpisode(segmentId)} ${title}`}>
            <AspectFrame ratio={aspectRatio}>
              <img
                src={assetUrl}
                alt={`${itemIdWithinEpisode(segmentId)} ${title}`}
                loading="lazy"
                className="size-full object-cover"
              />
            </AspectFrame>
          </PreviewableImageFrame>
        ) : (
          <div className="overflow-hidden rounded-lg ring-1 ring-border">
            <AspectFrame ratio={aspectRatio}>
              <PresentationPlayer
                key={`${segmentId}:${assetFp ?? "current"}`}
                projectName={projectName}
                resourceType="videos"
                resourceId={segmentId}
                posterPath={posterPath}
                {...playbackStart}
              />
            </AspectFrame>
          </div>
        )
      ) : (
        <AspectFrame ratio={aspectRatio}>
          <div className="flex size-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/30 text-muted-foreground">
            <Icon aria-hidden className="size-5" />
            <span className="text-xs">{t("media_not_generated")}</span>
          </div>
        </AspectFrame>
      )}

      {onGenerate && !demoReadOnly && (
        <Button
          className="mt-2.5 w-full"
          size="lg"
          onClick={onGenerate}
          disabled={generateDisabled || resourceBusy}
          title={
            generateDisabled
              ? (generateDisabledHint ?? t("media_generate_video_disabled_hint"))
              : undefined
          }
        >
          <Sparkles aria-hidden data-icon="inline-start" />
          {generateLabel}
          {estimatedCost && Object.values(estimatedCost).some((v) => v > 0) && (
            <span className="num text-xs font-normal">~{formatCost(estimatedCost)}</span>
          )}
        </Button>
      )}
    </div>
  );
}
