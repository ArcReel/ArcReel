import { useTranslation } from "react-i18next";
import { API } from "@/api";
import type { ReferenceVideoUnit } from "@/types";

export interface UnitPreviewPanelProps {
  unit: ReferenceVideoUnit | null;
  projectName?: string;
}

export function UnitPreviewPanel({ unit, projectName }: UnitPreviewPanelProps) {
  const { t } = useTranslation("dashboard");

  if (!unit) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-gray-500">
        {t("reference_preview_empty")}
      </div>
    );
  }

  const clip = unit.generated_assets.video_clip;
  const videoUrl = clip && projectName ? API.getFileUrl(projectName, clip) : null;

  return (
    <div className="flex h-full flex-col gap-3 p-3">
      <div className="aspect-video w-full overflow-hidden rounded-lg border border-gray-800 bg-black">
        {videoUrl ? (
          // eslint-disable-next-line jsx-a11y/media-has-caption -- AI-generated video clips have no caption track
          <video
            src={videoUrl}
            aria-label={t("reference_preview_video_aria", { id: unit.unit_id })}
            controls
            className="h-full w-full object-contain"
          />
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-gray-600">
            {t("reference_preview_empty")}
          </div>
        )}
      </div>

      <dl className="grid grid-cols-2 gap-1 text-xs text-gray-500">
        <dt>{t("reference_meta_unit")}</dt>
        <dd className="font-mono text-gray-300" translate="no">{unit.unit_id}</dd>
        <dt>{t("reference_meta_duration")}</dt>
        <dd className="tabular-nums text-gray-300">{unit.duration_seconds}s</dd>
        <dt>{t("reference_meta_shots")}</dt>
        <dd className="text-gray-300">{unit.shots.length}</dd>
        <dt>{t("reference_meta_references")}</dt>
        <dd className="text-gray-300">{unit.references.length}</dd>
      </dl>
    </div>
  );
}
