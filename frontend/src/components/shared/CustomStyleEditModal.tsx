import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ImagePlus, Loader2, Pencil, Trash2 } from "lucide-react";
import { API, type CustomStyle } from "@/api";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { errMsg } from "@/utils/async";
import { sanitizeImageSrc } from "@/utils/safe-url";

interface CustomStyleEditModalProps {
  style: CustomStyle;
  onClose: () => void;
  onSaved: (style: CustomStyle) => void;
}

export function CustomStyleEditModal({ style, onClose, onSaved }: CustomStyleEditModalProps) {
  const { t } = useTranslation(["common", "templates"]);
  const titleId = useId();
  const nameInputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const localPreviewRef = useRef<string | null>(null);
  const [name, setName] = useState(style.name);
  const [description, setDescription] = useState(style.description);
  const [image, setImage] = useState<File | null>(null);
  const [localPreview, setLocalPreview] = useState<string | null>(null);
  const [removeImage, setRemoveImage] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    nameInputRef.current?.focus();
  }, []);

  useEffect(() => () => {
    if (localPreviewRef.current) URL.revokeObjectURL(localPreviewRef.current);
  }, []);

  const existingPreview = removeImage
    ? null
    : API.getGlobalAssetUrl(style.image_path, style.updated_at);
  const displayedPreview = sanitizeImageSrc(localPreview ?? existingPreview);
  const hasResultingImage = Boolean(image || (!removeImage && style.image_path));
  const saveDisabled = saving
    || !name.trim()
    || (!description.trim() && !hasResultingImage);

  const handleImageChange = (file: File | null) => {
    if (localPreviewRef.current) URL.revokeObjectURL(localPreviewRef.current);
    const preview = file ? URL.createObjectURL(file) : null;
    localPreviewRef.current = preview;
    setImage(file);
    setLocalPreview(preview);
    if (file) setRemoveImage(false);
    setError(null);
  };

  const handleRemoveImage = () => {
    if (localPreviewRef.current) URL.revokeObjectURL(localPreviewRef.current);
    localPreviewRef.current = null;
    setImage(null);
    setLocalPreview(null);
    setRemoveImage(true);
    setError(null);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const result = await API.updateCustomStyle(style.id, {
        name: name.trim(),
        description,
        image,
        removeImage,
      });
      onSaved(result.style);
      onClose();
    } catch (cause: unknown) {
      setError(errMsg(cause));
    } finally {
      setSaving(false);
    }
  };

  return (
    <GlassModal
      open
      onClose={saving ? () => {} : onClose}
      labelledBy={titleId}
      widthClassName="w-[620px] max-w-[96vw]"
      closeOnBackdrop={!saving}
      closeOnEscape={!saving}
    >
      <div className="flex items-start gap-3 border-b border-hairline-soft px-6 py-5">
        <span
          aria-hidden
          className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-accent-soft bg-accent-dim text-accent-2"
        >
          <Pencil className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 id={titleId} className="display-serif text-[17px] font-semibold tracking-tight text-text">
            {t("templates:custom_style_edit_title")}
          </h2>
          <p className="mt-1 text-[11.5px] leading-relaxed text-text-3">
            {t("templates:custom_style_edit_snapshot_hint")}
          </p>
        </div>
        <ModalCloseButton onClick={onClose} disabled={saving} />
      </div>

      <div className="grid grid-cols-[210px_1fr] gap-5 p-6 max-sm:grid-cols-1">
        <div>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="focus-ring group relative aspect-[3/4] w-full overflow-hidden rounded-[10px] border border-dashed border-hairline bg-bg-grad-a/55"
          >
            {displayedPreview ? (
              <>
                <img src={displayedPreview} alt="" className="absolute inset-0 h-full w-full object-cover" />
                <span className="absolute inset-0 flex items-center justify-center gap-2 bg-black/60 text-[12px] text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
                  <ImagePlus className="h-4 w-4" aria-hidden />
                  {t("templates:custom_style_replace_image")}
                </span>
              </>
            ) : (
              <span className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-[11.5px] text-text-3">
                <ImagePlus className="h-5 w-5 text-accent-2" aria-hidden />
                {t("templates:custom_style_add_image")}
              </span>
            )}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".png,.jpg,.jpeg,.webp"
            className="hidden"
            onChange={(event) => {
              handleImageChange(event.target.files?.[0] ?? null);
              event.target.value = "";
            }}
          />
          {displayedPreview ? (
            <button
              type="button"
              onClick={handleRemoveImage}
              className="mt-2 inline-flex items-center gap-1.5 rounded-[6px] px-2 py-1 text-[11px] text-text-3 transition-colors hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <Trash2 className="h-3 w-3" aria-hidden />
              {t("templates:custom_style_remove_image")}
            </button>
          ) : null}
          <p className="mt-2 font-mono text-[9.5px] uppercase tracking-[0.12em] text-text-4">
            {t("templates:supported_formats")}
          </p>
        </div>

        <div className="space-y-4">
          <label className="block">
            <span className="mb-1.5 block text-[12px] font-medium text-text-2">
              {t("templates:custom_style_name_label")}
            </span>
            <input
              ref={nameInputRef}
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="focus-ring w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[12.5px] text-text outline-none"
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-[12px] font-medium text-text-2">
              {t("templates:custom_style_prompt_label")}
            </span>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={8}
              className="focus-ring w-full resize-y rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[12.5px] leading-[1.6] text-text outline-none"
              placeholder={t("templates:custom_style_prompt_placeholder")}
            />
          </label>
          {!description.trim() && !hasResultingImage ? (
            <p className="text-[11px] leading-relaxed text-warm">
              {t("templates:custom_style_content_required")}
            </p>
          ) : null}
          {error ? (
            <p role="alert" className="rounded-[7px] border border-warm/30 bg-warm/10 px-3 py-2 text-[11.5px] text-warm">
              {error}
            </p>
          ) : null}
        </div>
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-hairline-soft bg-bg-grad-a/35 px-6 py-4">
        <SecondaryButton size="sm" onClick={onClose} disabled={saving}>
          {t("common:cancel")}
        </SecondaryButton>
        <PrimaryButton
          size="sm"
          onClick={() => void handleSave()}
          disabled={saveDisabled}
          leadingIcon={saving ? <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" /> : undefined}
        >
          {saving ? t("templates:custom_style_saving") : t("templates:custom_style_save_changes")}
        </PrimaryButton>
      </div>
    </GlassModal>
  );
}
