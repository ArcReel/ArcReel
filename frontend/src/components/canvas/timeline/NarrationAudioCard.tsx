import { AudioLines, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { formatCost } from "@/utils/cost-format";
import type { CostBreakdown } from "@/types";
import { itemIdWithinEpisode } from "@/utils/episode-display";
import { VersionTimeMachine } from "./VersionTimeMachine";

interface NarrationAudioCardProps {
  projectName: string;
  segmentId: string;
  /** 只读小说原文（旁白文本来源） */
  novelText: string;
  /** narration_audio 相对路径，如 audio/segment_E1S01.wav */
  assetPath: string | null;
  /** 进行中状态 */
  generating?: boolean;
  /** 生成按钮是否禁用 */
  generateDisabled?: boolean;
  /** 自定义禁用 tooltip */
  generateDisabledHint?: string;
  /** 估算费用（按币种 breakdown） */
  estimatedCost?: CostBreakdown;
  /** 触发生成 */
  onGenerate?: () => void;
}

export function NarrationAudioCard({
  projectName,
  segmentId,
  novelText,
  assetPath,
  generating,
  generateDisabled,
  generateDisabledHint,
  estimatedCost,
  onGenerate,
}: NarrationAudioCardProps) {
  const { t } = useTranslation("dashboard");
  // 与 ShotDetail 的按钮禁用判定共用同一套 trim 规则，避免"卡片有正文、按钮却禁用"的矛盾态
  const hasNovelText = novelText.trim().length > 0;

  const assetFp = useProjectsStore((s) =>
    assetPath ? s.getAssetFingerprint(assetPath) : null,
  );
  const audioUrl = assetPath ? API.getFileUrl(projectName, assetPath, assetFp) : null;

  const generateLabel = assetPath
    ? t("media_regenerate_narration")
    : t("media_generate_narration");

  return (
    <div>
      <div className="mb-2 flex min-h-7 items-center gap-1.5">
        <AudioLines aria-hidden className="size-3.5 text-muted-foreground" />
        <h3 className="text-xs font-medium text-subtle-foreground">{t("media_narration_title")}</h3>
        <div className="ml-auto">
          <VersionTimeMachine
            projectName={projectName}
            resourceType="audio"
            resourceId={segmentId}
            iconOnly
            busy={Boolean(generating)}
          />
        </div>
      </div>

      {/* 只读原文 + 播放器 */}
      <div className="rounded-lg border border-border/50 border-l-2 border-l-primary/25 bg-muted/30 px-3 py-2.5">
        <p className="display-serif max-w-[40em] text-sm leading-relaxed whitespace-pre-wrap text-foreground">
          {hasNovelText ? novelText : t("no_original_text")}
        </p>

        {audioUrl ? (
          // eslint-disable-next-line jsx-a11y/media-has-caption -- 生成式旁白暂无字幕源，文本内容即上方只读原文
          <audio
            controls
            src={audioUrl}
            preload="metadata"
            aria-label={t("narration_audio_player_label", { id: itemIdWithinEpisode(segmentId) })}
            className="mt-2.5 h-9 w-full"
          />
        ) : (
          <div className="mt-2.5 flex items-center justify-center gap-2 rounded-md border border-dashed border-border bg-muted/30 py-2.5 text-xs text-muted-foreground">
            <AudioLines className="size-4" aria-hidden />
            <span>{t("media_not_generated")}</span>
          </div>
        )}
      </div>

      {onGenerate && (
        <Button
          className="mt-2.5 w-full"
          size="lg"
          onClick={onGenerate}
          disabled={generateDisabled || generating}
          title={generateDisabled ? generateDisabledHint : undefined}
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
