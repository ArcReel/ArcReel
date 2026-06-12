import { AudioLines, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { formatCost } from "@/utils/cost-format";
import type { CostBreakdown } from "@/types";

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
      {/* Header */}
      <div className="mb-2 flex items-center gap-1.5">
        <AudioLines className="h-3.5 w-3.5" style={{ color: "var(--color-text-3)" }} />
        <span
          className="text-[12px] font-semibold"
          style={{ color: "var(--color-text-2)" }}
        >
          {t("media_narration_title")}
        </span>
      </div>

      {/* 只读原文 + 播放器并排 */}
      <div
        className="rounded-[10px] px-3 py-2.5"
        style={{
          background:
            "linear-gradient(180deg, oklch(0.22 0.012 265 / 0.5), oklch(0.20 0.012 265 / 0.35))",
          border: "1px solid var(--color-hairline-soft)",
          borderLeft: "3px solid var(--color-accent-soft)",
        }}
      >
        <p
          className="display-serif m-0 text-[12.5px]"
          style={{ lineHeight: 1.65, color: "var(--color-text)" }}
        >
          {hasNovelText ? novelText : t("no_original_text")}
        </p>

        {audioUrl ? (
          // eslint-disable-next-line jsx-a11y/media-has-caption -- 生成式旁白暂无字幕源，文本内容即上方只读原文
          <audio
            controls
            src={audioUrl}
            preload="metadata"
            aria-label={t("narration_audio_player_label", { id: segmentId })}
            className="mt-2.5 h-9 w-full"
          />
        ) : (
          <div
            className="mt-2.5 flex items-center justify-center gap-2 rounded-[8px] py-2.5 text-[11.5px]"
            style={{
              border: "1px dashed var(--color-hairline)",
              background: "oklch(0.18 0.010 265 / 0.4)",
              color: "var(--color-text-4)",
            }}
          >
            <AudioLines className="h-4 w-4" aria-hidden />
            <span>{t("media_not_generated")}</span>
          </div>
        )}
      </div>

      {/* Generate CTA */}
      {onGenerate && (
        <button
          type="button"
          onClick={onGenerate}
          disabled={generateDisabled || generating}
          title={generateDisabled ? generateDisabledHint : undefined}
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
          {estimatedCost && Object.values(estimatedCost).some((v) => v > 0) && (
            <span className="num ml-1 text-[11px] opacity-70">
              ~{formatCost(estimatedCost)}
            </span>
          )}
        </button>
      )}
    </div>
  );
}
