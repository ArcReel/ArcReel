import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ImageIcon, Plus, Save, Trash2 } from "lucide-react";
import { enqueueReferenceKeyframe } from "@/actions/generation";
import { API } from "@/api";
import { ImageModelSelect, imageSelectionFromValue } from "@/components/shared/ImageModelSelect";
import { ImageEditButton } from "@/components/canvas/timeline/ImageEditButton";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useActiveResourceIds } from "@/stores/tasks-store";
import { useScrollTarget } from "@/hooks/useScrollTarget";
import { errMsg } from "@/utils/async";
import type { ReferenceKeyframe, ReferenceVideoUnit } from "@/types";

interface KeyframeCardProps {
  projectName: string;
  episode: number;
  scriptFile?: string;
  keyframe: ReferenceKeyframe;
  busy: boolean;
  onChanged: () => Promise<void>;
}

function KeyframeCard({
  projectName,
  episode,
  scriptFile,
  keyframe,
  busy,
  onChanged,
}: KeyframeCardProps) {
  const { t } = useTranslation("dashboard");
  const [description, setDescription] = useState(keyframe.description);
  const [model, setModel] = useState("");
  const [saving, setSaving] = useState(false);
  const fingerprint = useProjectsStore((state) =>
    keyframe.image_path ? state.getAssetFingerprint(keyframe.image_path) : null,
  );
  const imageUrl = keyframe.image_path
    ? API.getFileUrl(projectName, keyframe.image_path, fingerprint)
    : null;
  const dirty = description.trim() !== keyframe.description;

  useEffect(() => {
    // External Agent/browser edits arrive through the unit revision refresh. Keep a
    // local draft only while it differs from the latest durable description.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDescription(keyframe.description);
  }, [keyframe.description]);

  const save = async () => {
    const next = description.trim();
    if (!next || !dirty || saving || busy) return;
    setSaving(true);
    try {
      await API.patchReferenceKeyframe(projectName, episode, keyframe.keyframe_id, next);
      await onChanged();
    } catch (error) {
      useAppStore.getState().pushToast(errMsg(error), "error");
    } finally {
      setSaving(false);
    }
  };

  const generate = async () => {
    try {
      await enqueueReferenceKeyframe(
        projectName,
        episode,
        keyframe.keyframe_id,
        imageSelectionFromValue(model),
      );
    } catch (error) {
      useAppStore.getState().pushToast(errMsg(error), "error");
    }
  };

  const remove = async () => {
    if (busy || !window.confirm(t("reference_keyframe_delete_confirm"))) return;
    try {
      await API.deleteReferenceKeyframe(projectName, episode, keyframe.keyframe_id);
      await onChanged();
    } catch (error) {
      useAppStore.getState().pushToast(errMsg(error), "error");
    }
  };

  return (
    <article
      id={`reference_keyframe-${keyframe.keyframe_id}`}
      className="rounded-xl border border-[var(--color-hairline-soft)] bg-[oklch(0.20_0.011_265_/_0.55)] p-4"
    >
      <header className="mb-3 flex items-center gap-2">
        <span className="grid h-7 w-7 place-items-center rounded-md border border-[var(--color-accent-soft)] bg-[var(--color-accent-dim)] text-[var(--color-accent-2)]">
          <ImageIcon className="h-3.5 w-3.5" aria-hidden="true" />
        </span>
        <strong className="text-[13px] text-[var(--color-text)]" translate="no">
          {keyframe.keyframe_id}
        </strong>
        <span className="flex-1" />
        <ImageEditButton
          projectName={projectName}
          resourceType="reference_keyframe"
          resourceId={keyframe.keyframe_id}
          scriptFile={scriptFile}
          hasImage={Boolean(keyframe.image_path)}
          busy={busy}
        />
        <VersionTimeMachine
          projectName={projectName}
          resourceType="keyframes"
          resourceId={keyframe.keyframe_id}
          iconOnly
          busy={busy}
          onRestore={onChanged}
        />
        <button
          type="button"
          onClick={() => void remove()}
          disabled={busy}
          aria-label={t("reference_keyframe_delete")}
          title={t("reference_keyframe_delete")}
          className="focus-ring inline-grid h-7 w-7 place-items-center rounded-md text-[var(--color-text-3)] hover:bg-red-500/10 hover:text-red-300 disabled:opacity-40"
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </header>

      {imageUrl ? (
        <PreviewableImageFrame src={imageUrl} alt={keyframe.description}>
          <AspectFrame ratio="16:9">
            <img
              src={imageUrl}
              alt={keyframe.description}
              loading="lazy"
              className="h-full w-full object-cover"
            />
          </AspectFrame>
        </PreviewableImageFrame>
      ) : (
        <AspectFrame ratio="16:9">
          <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-[var(--color-hairline)] text-xs text-[var(--color-text-4)]">
            {t("reference_keyframe_not_generated")}
          </div>
        </AspectFrame>
      )}

      <label className="mt-3 block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--color-text-4)]">
        {t("reference_keyframe_description")}
        <textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          rows={4}
          disabled={busy}
          className="focus-ring mt-1.5 w-full resize-y rounded-lg border border-[var(--color-hairline)] bg-[oklch(0.18_0.010_265_/_0.55)] px-3 py-2 text-[13px] font-normal leading-6 normal-case tracking-normal text-[var(--color-text)]"
        />
      </label>
      {dirty && (
        <button
          type="button"
          onClick={() => void save()}
          disabled={saving || busy || !description.trim()}
          className="focus-ring mt-2 inline-flex items-center gap-1.5 rounded-md border border-[var(--color-hairline)] px-2.5 py-1 text-xs text-[var(--color-text-2)] disabled:opacity-40"
        >
          <Save className="h-3.5 w-3.5" aria-hidden="true" />
          {t("common:save")}
        </button>
      )}
      <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
        <ImageModelSelect value={model} onChange={setModel} capability="any" />
        <GenerateButton
          onClick={() => void generate()}
          loading={busy}
          label={
            keyframe.image_path
              ? t("reference_keyframe_regenerate")
              : t("reference_keyframe_generate")
          }
          className="justify-center"
        />
      </div>
    </article>
  );
}

interface KeyframePreviewPanelProps {
  projectName: string;
  episode: number;
  unit: ReferenceVideoUnit;
  scriptFile?: string;
  onChanged: () => Promise<void>;
}

export function KeyframePreviewPanel({
  projectName,
  episode,
  unit,
  scriptFile,
  onChanged,
}: KeyframePreviewPanelProps) {
  const { t } = useTranslation("dashboard");
  useScrollTarget("reference_keyframe");
  const [adding, setAdding] = useState(false);
  const [newDescription, setNewDescription] = useState("");
  const activeIds = useActiveResourceIds("reference_keyframe", projectName);
  const keyframes = unit.keyframes ?? [];

  if (unit.storyboard_sheet?.status !== "confirmed") {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <p className="rounded-lg border border-amber-400/25 bg-amber-400/5 px-4 py-8 text-center text-xs leading-6 text-amber-200">
          {t("reference_keyframe_storyboard_gate")}
        </p>
      </div>
    );
  }

  const add = async () => {
    const description = newDescription.trim();
    if (!description || adding || keyframes.length >= 5) return;
    setAdding(true);
    try {
      await API.addReferenceKeyframe(projectName, episode, unit.unit_id, description);
      setNewDescription("");
      await onChanged();
    } catch (error) {
      useAppStore.getState().pushToast(errMsg(error), "error");
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-3">
      <div className="grid gap-3 xl:grid-cols-2">
        {keyframes.map((keyframe) => (
          <KeyframeCard
            key={keyframe.keyframe_id}
            projectName={projectName}
            episode={episode}
            scriptFile={scriptFile}
            keyframe={keyframe}
            busy={activeIds.has(keyframe.keyframe_id)}
            onChanged={onChanged}
          />
        ))}
      </div>
      {keyframes.length === 0 && (
        <p className="rounded-lg border border-dashed border-[var(--color-hairline)] px-4 py-8 text-center text-xs text-[var(--color-text-4)]">
          {t("reference_keyframe_empty")}
        </p>
      )}
      {keyframes.length < 5 && (
        <div className="mt-3 rounded-xl border border-dashed border-[var(--color-hairline)] p-3">
          <textarea
            value={newDescription}
            onChange={(event) => setNewDescription(event.target.value)}
            rows={3}
            placeholder={t("reference_keyframe_add_placeholder")}
            className="focus-ring w-full resize-y rounded-lg border border-[var(--color-hairline)] bg-[oklch(0.18_0.010_265_/_0.55)] px-3 py-2 text-[13px] text-[var(--color-text)]"
          />
          <button
            type="button"
            onClick={() => void add()}
            disabled={adding || !newDescription.trim()}
            className="focus-ring mt-2 inline-flex items-center gap-1.5 rounded-md border border-[var(--color-hairline)] px-3 py-1.5 text-xs text-[var(--color-text-2)] disabled:opacity-40"
          >
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            {t("reference_keyframe_add")}
          </button>
        </div>
      )}
    </div>
  );
}
