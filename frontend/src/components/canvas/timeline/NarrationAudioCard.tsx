import { useRef, useState } from "react";
import { isResourceBusy } from "@/stores/tasks-store";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { AudioLines, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { formatCost } from "@/utils/cost-format";
import type { CostBreakdown } from "@/types";
import { useItemPositionLabel } from "@/components/shared/episode-item/useEpisodeItem";
import { VersionTimeMachine } from "./VersionTimeMachine";

interface NarrationAudioCardProps {
  readOnly?: boolean;
  projectName: string;
  segmentId: string;
  /** Read-only novel source for narration text. */
  novelText: string;
  /** Relative narration_audio path, e.g. audio/segment_E1S01.wav. */
  assetPath: string | null;
  /** In-progress state. */
  generating?: boolean;
  /** Whether generation is disabled. */
  generateDisabled?: boolean;
  /** Custom disabled tooltip. */
  generateDisabledHint?: string;
  /** Estimated costs broken down by currency. */
  estimatedCost?: CostBreakdown;
  /** Trigger generation. */
  onGenerate?: () => void;
  /** Generation label defaults by artifact presence to generate/regenerate; unsaved changes pass save-and-generate. */
  generateLabel?: string;
}

export function NarrationAudioCard({
  readOnly = false,
  projectName,
  segmentId,
  novelText,
  assetPath,
  generating,
  generateDisabled,
  generateDisabledHint,
  estimatedCost,
  onGenerate,
  generateLabel: generateLabelOverride,
}: NarrationAudioCardProps) {
  const { t } = useTranslation("dashboard");
  const itemLabel = useItemPositionLabel(segmentId);
  const demo = useDemoWorkbench();
  const [restoring, setRestoring] = useState(false);
  const restoringRef = useRef(false);
  const checkBusy = () => restoringRef.current || isResourceBusy("tts", projectName, segmentId);
  const onRestoringChange = (next: boolean) => { restoringRef.current = next; setRestoring(next); };
  // Share trim rules with ShotDetail disabled checks, avoiding visible-body but disabled-button contradictions.
  const hasNovelText = novelText.trim().length > 0;

  const assetFp = useProjectsStore((s) =>
    assetPath ? s.getAssetFingerprint(assetPath) : null,
  );
  const audioUrl = assetPath ? API.getFileUrl(projectName, assetPath, assetFp) : null;

  const generateLabel =
    generateLabelOverride ?? (assetPath ? t("media_regenerate_narration") : t("media_generate_narration"));

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
            readOnly={readOnly || demo}
            busy={Boolean(generating) || restoring}
            checkBusy={checkBusy}
            onRestoringChange={onRestoringChange}
          />
        </div>
      </div>

      {/* Read-only source plus player. */}
      <div className="rounded-lg border border-border/50 border-l-2 border-l-primary/25 bg-muted/30 px-3 py-2.5">
        <p className="display-serif max-w-[40em] text-sm leading-relaxed whitespace-pre-wrap text-foreground">
          {hasNovelText ? novelText : t("no_original_text")}
        </p>

        {audioUrl ? (
          // eslint-disable-next-line jsx-a11y/media-has-caption -- Generated narration has no subtitle source; text is the read-only source above
          <audio
            controls
            src={audioUrl}
            preload="metadata"
            aria-label={t("narration_audio_player_label", { id: itemLabel })}
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
          disabled={generateDisabled || generating || restoring || readOnly || demo}
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
