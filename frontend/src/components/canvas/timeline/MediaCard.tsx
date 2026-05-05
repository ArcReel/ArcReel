import { Sparkles, ImageIcon, Film } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { ImageFlipReveal } from "@/components/ui/ImageFlipReveal";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { VersionTimeMachine } from "./VersionTimeMachine";

type MediaKind = "storyboard" | "video";

interface MediaCardProps {
  kind: MediaKind;
  projectName: string;
  segmentId: string;
  /** 资产相对路径，如 storyboards/E1S2_v1.png */
  assetPath: string | null;
  /** 视频海报缩略图（仅 kind=video 用） */
  posterPath?: string | null;
  /** 渲染比例 */
  aspectRatio: "9:16" | "16:9";
  /** 是否在 grid 模式下隐藏单独生成按钮 */
  hideGenerateButton?: boolean;
  /** 生成按钮是否禁用（视频生成需要先有分镜图） */
  generateDisabled?: boolean;
  /** 自定义禁用 tooltip，未提供时使用默认（"分镜图未生成"）的视频禁用提示 */
  generateDisabledHint?: string;
  /** 进行中状态 */
  generating?: boolean;
  /** 估算费用（美元） */
  estimatedCost?: number;
  /** 触发生成 */
  onGenerate?: () => void;
  /** 版本恢复回调 */
  onRestore?: () => Promise<void> | void;
}

export function MediaCard({
  kind,
  projectName,
  segmentId,
  assetPath,
  posterPath,
  aspectRatio,
  hideGenerateButton,
  generateDisabled,
  generateDisabledHint,
  generating,
  estimatedCost,
  onGenerate,
  onRestore,
}: MediaCardProps) {
  const { t } = useTranslation("dashboard");

  const assetFp = useProjectsStore((s) =>
    assetPath ? s.getAssetFingerprint(assetPath) : null,
  );
  const posterFp = useProjectsStore((s) =>
    posterPath ? s.getAssetFingerprint(posterPath) : null,
  );
  const assetUrl = assetPath ? API.getFileUrl(projectName, assetPath, assetFp) : null;
  const posterUrl = posterPath
    ? API.getFileUrl(projectName, posterPath, posterFp)
    : null;

  const Icon = kind === "storyboard" ? ImageIcon : Film;
  const title =
    kind === "storyboard" ? t("media_storyboard_title") : t("media_video_title");
  const generateLabel =
    kind === "storyboard"
      ? assetPath
        ? t("media_regenerate_storyboard")
        : t("media_generate_storyboard")
      : assetPath
        ? t("media_regenerate_video")
        : t("media_generate_video");
  const resourceType: "storyboards" | "videos" =
    kind === "storyboard" ? "storyboards" : "videos";

  return (
    <div>
      {/* Header */}
      <div className="mb-2 flex items-center gap-1.5">
        <Icon className="h-3.5 w-3.5" style={{ color: "var(--color-text-3)" }} />
        <span
          className="text-[12px] font-semibold"
          style={{ color: "var(--color-text-2)" }}
        >
          {title}
        </span>
        <span className="flex-1" />
        <VersionTimeMachine
          projectName={projectName}
          resourceType={resourceType}
          resourceId={segmentId}
          onRestore={onRestore}
        />
      </div>

      {/* Media */}
      {assetUrl ? (
        kind === "storyboard" ? (
          <PreviewableImageFrame src={assetUrl} alt={`${segmentId} ${title}`}>
            <AspectFrame ratio={aspectRatio}>
              <ImageFlipReveal
                src={assetUrl}
                alt={`${segmentId} ${title}`}
                loading="lazy"
                className="h-full w-full object-cover"
                fallback={null}
              />
            </AspectFrame>
          </PreviewableImageFrame>
        ) : (
          <div
            className="overflow-hidden rounded-[10px]"
            style={{
              boxShadow:
                "0 16px 40px -16px oklch(0 0 0 / 0.7), 0 0 0 1px var(--color-hairline)",
            }}
          >
            <AspectFrame ratio={aspectRatio}>
              {/* eslint-disable-next-line jsx-a11y/media-has-caption -- 生成式预览视频暂无字幕源 */}
              <video
                src={assetUrl}
                poster={posterUrl ?? undefined}
                controls
                playsInline
                className="h-full w-full object-cover"
                preload="metadata"
              />
            </AspectFrame>
          </div>
        )
      ) : (
        <AspectFrame ratio={aspectRatio}>
          <div
            className="flex h-full w-full flex-col items-center justify-center gap-2 rounded-[10px]"
            style={{
              border: "1px dashed var(--color-hairline)",
              background: "oklch(0.18 0.010 265 / 0.4)",
              color: "var(--color-text-4)",
            }}
          >
            <Icon className="h-5 w-5" />
            <span className="text-[11.5px]">{t("media_not_generated")}</span>
          </div>
        </AspectFrame>
      )}

      {/* Generate CTA */}
      {!hideGenerateButton && onGenerate && (
        <button
          type="button"
          onClick={onGenerate}
          disabled={generateDisabled || generating}
          title={
            generateDisabled
              ? (generateDisabledHint ?? t("media_generate_video_disabled_hint"))
              : undefined
          }
          className="mt-2.5 inline-flex w-full items-center justify-center gap-1.5 rounded-[10px] px-3.5 py-2.5 text-[13px] font-semibold transition-opacity focus-ring disabled:cursor-not-allowed disabled:opacity-50"
          style={{
            color: "oklch(0.14 0 0)",
            background: "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
            boxShadow:
              "inset 0 1px 0 oklch(1 0 0 / 0.3), 0 4px 14px -4px var(--color-accent-glow)",
          }}
        >
          <Sparkles className="h-3.5 w-3.5" />
          <span>{generateLabel}</span>
          {estimatedCost != null && estimatedCost > 0 && (
            <span
              className="num ml-1 text-[11px] opacity-70"
            >
              ~${estimatedCost.toFixed(estimatedCost < 0.01 ? 3 : 2)}
            </span>
          )}
        </button>
      )}
    </div>
  );
}
