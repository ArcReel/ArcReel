import { useState, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { Asset, AssetType } from "@/types/asset";

type Mode = "create" | "edit" | "import";
type Scope = "project" | "library";

interface Props {
  type: AssetType;
  mode: Mode;
  scope: Scope;
  initialData?: Partial<Asset>;
  conflictWith?: Asset;
  targetProject?: string;
  onClose: () => void;
  onSubmit: (payload: {
    name: string;
    description: string;
    voice_style: string;
    image?: File | null;
    overwrite?: boolean;
  }) => Promise<void>;
}

export function AssetFormModal({
  type, mode, scope: _scope, initialData, conflictWith, onClose, onSubmit,
}: Props) {
  const { t } = useTranslation("assets");
  const [name, setName] = useState(initialData?.name ?? "");
  const [description, setDescription] = useState(initialData?.description ?? "");
  const [voiceStyle, setVoiceStyle] = useState(initialData?.voice_style ?? "");
  const [image, setImage] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const isCharacter = type === "character";
  const title = mode === "create" ? t("create_title", { type: t(`type.${type}`) })
    : mode === "edit" ? t("edit_title", { type: t(`type.${type}`), name: initialData?.name })
    : t("import_title", { name: initialData?.name });

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
    <div role="dialog" className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[480px] max-w-[96vw] rounded-lg bg-gray-900 border border-gray-700 shadow-2xl">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-800 bg-gray-950/60">
          <h3 className="flex-1 text-sm font-semibold text-white">{title}</h3>
          <button onClick={onClose} aria-label={t("close")} className="text-gray-500 hover:text-gray-300">✕</button>
        </div>

        {conflictWith && (
          <div className="px-4 py-2 bg-amber-950 border-l-2 border-amber-600 text-xs text-amber-200">
            {t("conflict_warning", { name: conflictWith.name })}
          </div>
        )}

        <div className="grid grid-cols-[160px_1fr] gap-4 p-4">
          <div className="flex flex-col gap-2">
            <button type="button" onClick={() => fileRef.current?.click()}
              className="aspect-[3/4] border border-dashed border-gray-700 rounded flex flex-col items-center justify-center text-gray-500 text-xs hover:border-gray-500">
              {image ? image.name : t("upload_image_optional")}
            </button>
            <input ref={fileRef} type="file" accept=".png,.jpg,.jpeg,.webp" className="hidden"
              onChange={(e) => setImage(e.target.files?.[0] ?? null)} />
          </div>
          <div className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-xs text-gray-400">
              {t("field.name")} *
              <input value={name} onChange={(e) => setName(e.target.value)}
                className="bg-gray-950 border border-gray-800 rounded px-2 py-1 text-sm text-gray-200" />
            </label>
            <label className="flex flex-col gap-1 text-xs text-gray-400">
              {t("field.description")}
              <textarea value={description} onChange={(e) => setDescription(e.target.value)}
                rows={3}
                className="bg-gray-950 border border-gray-800 rounded px-2 py-1 text-sm text-gray-200" />
            </label>
            {isCharacter && (
              <label className="flex flex-col gap-1 text-xs text-gray-400">
                {t("field.voice_style")}
                <input value={voiceStyle} onChange={(e) => setVoiceStyle(e.target.value)}
                  className="bg-gray-950 border border-gray-800 rounded px-2 py-1 text-sm text-gray-200" />
              </label>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 px-4 py-3 border-t border-gray-800 bg-gray-950/60">
          <button onClick={onClose} className="px-3 py-1 text-xs rounded bg-gray-800 text-gray-300">
            {t("cancel")}
          </button>
          {mode === "import" && conflictWith && (
            <button onClick={() => void submit(true)} disabled={submitting}
              className="px-3 py-1 text-xs rounded bg-gray-700 text-white">
              {t("overwrite_existing")}
            </button>
          )}
          <button onClick={() => void submit(false)} disabled={submitting || !name.trim()}
            aria-label={mode === "create" ? t("create") : mode === "edit" ? t("save") : t("confirm_import")}
            className="ml-auto px-3 py-1 text-xs rounded bg-indigo-600 text-white disabled:opacity-50">
            {mode === "create" ? t("create") : mode === "edit" ? t("save") : t("confirm_import")}
          </button>
        </div>
      </div>
    </div>
  );
}
