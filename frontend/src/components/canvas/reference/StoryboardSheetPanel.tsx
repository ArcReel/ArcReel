import { useState } from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle2, Images, Loader2 } from "lucide-react";
import { enqueueReferenceStoryboardSheet } from "@/actions/generation";
import { API } from "@/api";
import { ImageEditButton } from "@/components/canvas/timeline/ImageEditButton";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { ImageModelSelect, imageSelectionFromValue } from "@/components/shared/ImageModelSelect";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useActiveResourceIds } from "@/stores/tasks-store";
import { errMsg } from "@/utils/async";
import type { ReferenceVideoUnit } from "@/types";

interface StoryboardSheetPanelProps {
  projectName: string;
  episode: number;
  unit: ReferenceVideoUnit;
  scriptFile?: string;
  onChanged: () => Promise<void>;
}

export function StoryboardSheetPanel({
  projectName,
  episode,
  unit,
  scriptFile,
  onChanged,
}: StoryboardSheetPanelProps) {
  const { t } = useTranslation("dashboard");
  const [model, setModel] = useState("");
  const [confirming, setConfirming] = useState(false);
  const activeIds = useActiveResourceIds("reference_storyboard_sheet", projectName);
  const keyframeActiveIds = useActiveResourceIds("reference_keyframe", projectName);
  const busy = activeIds.has(unit.unit_id);
  const keyframesBusy = (unit.keyframes ?? []).some((item) => keyframeActiveIds.has(item.keyframe_id));
  const sheet = unit.storyboard_sheet;
  const fingerprint = useProjectsStore((state) =>
    sheet?.image_path ? state.getAssetFingerprint(sheet.image_path) : null,
  );
  const imageUrl = sheet?.image_path
    ? API.getFileUrl(projectName, sheet.image_path, fingerprint)
    : null;

  const generate = async () => {
    try {
      await enqueueReferenceStoryboardSheet(
        projectName,
        episode,
        unit.unit_id,
        imageSelectionFromValue(model),
      );
    } catch (error) {
      useAppStore.getState().pushToast(errMsg(error), "error");
    }
  };

  const confirm = async () => {
    if (!sheet || sheet.status === "confirmed" || confirming || busy) return;
    setConfirming(true);
    try {
      const response = await API.confirmReferenceStoryboardSheet(projectName, episode, unit.unit_id);
      useAppStore.getState().pushToast(response.message, "success");
      await onChanged();
    } catch (error) {
      useAppStore.getState().pushToast(errMsg(error), "error");
    } finally {
      setConfirming(false);
    }
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-3">
      <article className="rounded-xl border border-[var(--color-hairline-soft)] bg-[oklch(0.20_0.011_265_/_0.55)] p-4">
        <header className="mb-3 flex items-center gap-2">
          <span className="grid h-7 w-7 place-items-center rounded-md border border-[var(--color-accent-soft)] bg-[var(--color-accent-dim)] text-[var(--color-accent-2)]">
            <Images className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
          <strong className="text-[13px] text-[var(--color-text)]">
            {t("reference_storyboard_sheet_title")}
          </strong>
          <span className="flex-1" />
          {sheet?.status === "confirmed" ? (
            <span className="inline-flex items-center gap-1 text-xs text-emerald-300">
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
              {t("reference_storyboard_sheet_confirmed_badge")}
            </span>
          ) : sheet ? (
            <span className="text-xs text-amber-300">
              {t("reference_storyboard_sheet_pending_badge")}
            </span>
          ) : null}
          <ImageEditButton
            projectName={projectName}
            resourceType="reference_storyboard_sheet"
            resourceId={unit.unit_id}
            scriptFile={scriptFile}
            hasImage={Boolean(sheet?.image_path)}
            busy={busy || confirming || keyframesBusy}
          />
          <VersionTimeMachine
            projectName={projectName}
            resourceType="storyboard_sheets"
            resourceId={unit.unit_id}
            iconOnly
            busy={busy || confirming || keyframesBusy}
            onRestore={onChanged}
          />
        </header>

        {imageUrl ? (
          <PreviewableImageFrame src={imageUrl} alt={t("reference_storyboard_sheet_title")}>
            <img
              src={imageUrl}
              alt={t("reference_storyboard_sheet_title")}
              loading="lazy"
              className="max-h-[70vh] w-full rounded-lg object-contain"
            />
          </PreviewableImageFrame>
        ) : (
          <div className="flex min-h-64 items-center justify-center rounded-lg border border-dashed border-[var(--color-hairline)] text-center text-xs text-[var(--color-text-4)]">
            {busy ? (
              <span className="inline-flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                {t("reference_storyboard_sheet_generating")}
              </span>
            ) : (
              t("reference_storyboard_sheet_not_generated")
            )}
          </div>
        )}

        <p className="mt-3 text-xs leading-5 text-[var(--color-text-3)]">
          {t("reference_storyboard_sheet_help")}
        </p>

        <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
          <ImageModelSelect value={model} onChange={setModel} capability="any" />
          <GenerateButton
            onClick={() => void generate()}
            loading={busy}
            label={
              sheet
                ? t("reference_storyboard_sheet_regenerate")
                : t("reference_storyboard_sheet_generate")
            }
            className="justify-center"
          />
        </div>

        {sheet && sheet.status !== "confirmed" && (
          <button
            type="button"
            onClick={() => void confirm()}
            disabled={busy || confirming || keyframesBusy}
            className="focus-ring mt-3 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-[var(--color-accent)] px-4 py-2.5 text-sm font-semibold text-[var(--color-accent-contrast)] disabled:opacity-40"
          >
            {confirming && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {t("reference_storyboard_sheet_confirm_and_generate")}
          </button>
        )}
      </article>
    </div>
  );
}
