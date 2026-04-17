import { useEffect, useState, useRef } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ImagePlus, Landmark, Package, User, X } from "lucide-react";
import type { Asset, AssetType } from "@/types/asset";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { sanitizeImageSrc } from "@/utils/safe-url";

type Mode = "create" | "edit" | "import";

interface Props {
  type: AssetType;
  mode: Mode;
  initialData?: Partial<Asset>;
  previewImageUrl?: string;
  conflictWith?: Asset;
  onClose: () => void;
  onSubmit: (payload: {
    name: string;
    description: string;
    voice_style: string;
    image?: File | null;
    overwrite?: boolean;
  }) => Promise<void>;
}

const TYPE_ICON: Record<AssetType, React.ComponentType<{ className?: string }>> = {
  character: User,
  scene: Landmark,
  prop: Package,
};

export function AssetFormModal({
  type, mode, initialData, previewImageUrl, conflictWith, onClose, onSubmit,
}: Props) {
  const { t } = useTranslation("assets");
  const [name, setName] = useState(initialData?.name ?? "");
  const [description, setDescription] = useState(initialData?.description ?? "");
  const [voiceStyle, setVoiceStyle] = useState(initialData?.voice_style ?? "");
  const [image, setImage] = useState<File | null>(null);
  const [localPreview, setLocalPreview] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef);

  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  useEscapeClose(onClose);

  useEffect(() => {
    if (!image) {
      setLocalPreview(null);
      return;
    }
    const url = URL.createObjectURL(image);
    setLocalPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [image]);

  const displayedPreview = sanitizeImageSrc(localPreview ?? previewImageUrl);
  const TypeIcon = TYPE_ICON[type];

  const isCharacter = type === "character";
  const typeLabel = t(`type.${type}`);
  const title = mode === "create" ? t("create_title", { type: typeLabel })
    : mode === "edit" ? t("edit_title", { type: typeLabel, name: initialData?.name })
    : t("import_title", { name: initialData?.name });

  const primaryLabel = mode === "create" ? t("create") : mode === "edit" ? t("save") : t("confirm_import");

  const submit = async (overwrite = false) => {
    setSubmitting(true);
    try {
      await onSubmit({ name: name.trim(), description, voice_style: voiceStyle, image, overwrite });
      onClose();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center px-4">
      <button
        type="button"
        aria-label={t("close")}
        onClick={onClose}
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="relative w-[560px] max-w-[96vw] overflow-hidden rounded-2xl border border-gray-700/80 bg-gray-900 shadow-2xl shadow-black/60"
      >
        {/* Header */}
        <div className="relative flex items-start gap-3 border-b border-gray-800 bg-gradient-to-b from-gray-900 to-gray-950 px-6 py-5">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-indigo-500/30 bg-indigo-500/10 text-indigo-300">
            <TypeIcon className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-base font-semibold text-white">{title}</h3>
            <p className="mt-0.5 text-xs text-gray-500">
              {mode === "import" ? t("library_subtitle") : typeLabel}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("close")}
            className="rounded-md p-1 text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-200"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Conflict warning */}
        {conflictWith && (
          <div className="flex items-start gap-2 border-b border-amber-500/20 bg-amber-500/10 px-6 py-3 text-xs text-amber-200">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" />
            <span>{t("conflict_warning", { name: conflictWith.name })}</span>
          </div>
        )}

        {/* Body */}
        <div className="grid grid-cols-[200px_1fr] gap-5 p-6">
          {/* Image uploader */}
          <div>
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="group relative aspect-[3/4] w-full overflow-hidden rounded-xl border border-dashed border-gray-700 bg-gray-950/60 transition-colors hover:border-indigo-500/60 focus-ring"
            >
              {displayedPreview ? (
                <>
                  <img
                    src={displayedPreview}
                    alt=""
                    className="absolute inset-0 h-full w-full object-cover"
                  />
                  <div className="absolute inset-0 flex items-center justify-center gap-2 bg-black/60 text-sm text-white opacity-0 transition-opacity group-hover:opacity-100">
                    <ImagePlus className="h-4 w-4" />
                    {t("replace_image")}
                  </div>
                </>
              ) : (
                <div className="flex h-full w-full flex-col items-center justify-center gap-2 px-4 text-center text-gray-500 transition-colors group-hover:text-gray-300">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-gray-800 text-gray-400">
                    <ImagePlus className="h-4 w-4" />
                  </div>
                  <span className="text-xs">{t("upload_image_hint")}</span>
                  <span className="text-[10px] text-gray-600">{t("upload_image_optional")}</span>
                </div>
              )}
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".png,.jpg,.jpeg,.webp"
              className="hidden"
              onChange={(e) => setImage(e.target.files?.[0] ?? null)}
            />
          </div>

          {/* Form fields */}
          <div className="flex flex-col gap-4">
            <label className="flex flex-col gap-1.5 text-[11px] font-medium uppercase tracking-wider text-gray-500">
              <span>
                {t("field.name")} <span className="text-indigo-300">*</span>
              </span>
              <input
                ref={nameRef}
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="rounded-lg border border-gray-800 bg-gray-950 px-3 py-2 text-sm text-gray-100 transition-colors placeholder:text-gray-600 focus:border-indigo-500/60 focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1.5 text-[11px] font-medium uppercase tracking-wider text-gray-500">
              {t("field.description")}
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={4}
                className="resize-none rounded-lg border border-gray-800 bg-gray-950 px-3 py-2 text-sm text-gray-100 leading-5 transition-colors focus:border-indigo-500/60 focus:outline-none"
              />
            </label>
            {isCharacter && (
              <label className="flex flex-col gap-1.5 text-[11px] font-medium uppercase tracking-wider text-gray-500">
                {t("field.voice_style")}
                <input
                  value={voiceStyle}
                  onChange={(e) => setVoiceStyle(e.target.value)}
                  className="rounded-lg border border-gray-800 bg-gray-950 px-3 py-2 text-sm text-gray-100 transition-colors focus:border-indigo-500/60 focus:outline-none"
                />
              </label>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center gap-2 border-t border-gray-800 bg-gray-950/80 px-6 py-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-gray-800 px-4 py-1.5 text-sm text-gray-300 transition-colors hover:border-gray-600 hover:text-white"
          >
            {t("cancel")}
          </button>
          {mode === "import" && conflictWith && (
            <button
              type="button"
              onClick={() => void submit(true)}
              disabled={submitting}
              className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-1.5 text-sm text-amber-200 transition-colors hover:bg-amber-500/20 disabled:opacity-50"
            >
              {t("overwrite_existing")}
            </button>
          )}
          <button
            type="button"
            onClick={() => void submit(false)}
            disabled={submitting || !name.trim()}
            className="ml-auto rounded-lg bg-indigo-600 px-5 py-1.5 text-sm font-medium text-white shadow-lg shadow-indigo-900/40 transition-all hover:bg-indigo-500 hover:shadow-indigo-700/50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {primaryLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
