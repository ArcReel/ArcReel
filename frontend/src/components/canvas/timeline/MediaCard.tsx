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
import { useItemPositionLabel } from "@/components/shared/episode-item/useEpisodeItem";
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
  assetPath: string | null;
  posterPath?: string | null;
  aspectRatio: "9:16" | "16:9";
  generateDisabled?: boolean;
  generateDisabledHint?: string;
  generating?: boolean;
  estimatedCost?: CostBreakdown;
  onGenerate?: () => void;
  generateLabel?: string;
  onRestore?: () => Promise<unknown> | void;
  onUpload?: (file: File) => Promise<void> | void;
  uploading?: boolean;
  uploadDisabled?: boolean;
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
  const itemLabel = useItemPositionLabel(segmentId);
  const [restoring, setRestoring] = useState(false);
  const restoringRef = useRef(false);
  const freshBusy = () => restoringRef.current || Boolean(checkBusy?.()) || isResourceBusy(kind, projectName, segmentId);
  const setRestoreBusy = (next: boolean) => {
    restoringRef.current = next;
    setRestoring(next);
    onRestoringChange?.(next);
  };
  const demoReadOnly = useDemoWorkbench();

  const assetFp = useProjectsStore((s) =>
    assetPath ? s.getAssetFingerprint(assetPath) : null,
  );
  const playbackStart = usePlaybackStart("videos", segmentId);
  const assetUrl = assetPath ? API.getFileUrl(projectName, assetPath, assetFp) : null;
  const storyboardSrcSet =
    assetPath && kind === "storyboard"
      ? `${API.getFileUrl(projectName, assetPath, assetFp, { width: 640 })} 640w, ${API.getFileUrl(projectName, assetPath, assetFp, { width: 1280 })} 1280w`
      : undefined;

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
          <PreviewableImageFrame src={assetUrl} alt={`${itemLabel} ${title}`}>
            <AspectFrame ratio={aspectRatio}>
              <img
                src={assetUrl}
                srcSet={storyboardSrcSet}
                sizes="(min-width: 1280px) 720px, 640px"
                alt={`${itemLabel} ${title}`}
                loading="lazy"
                decoding="async"
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
