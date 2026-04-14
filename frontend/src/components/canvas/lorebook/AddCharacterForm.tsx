import { useEffect, useId, useRef, useState } from "react";
import { useAutoFocus } from "@/hooks/useAutoFocus";
import { voidPromise } from "@/utils/async";
import { ImagePlus, Loader2, Upload, X } from "lucide-react";
import { useTranslation } from "react-i18next";

interface AddCharacterFormProps {
  onSubmit: (
    name: string,
    description: string,
    voiceStyle: string,
    referenceFile?: File | null,
  ) => Promise<void>;
  onCancel: () => void;
}

export function AddCharacterForm({ onSubmit, onCancel }: AddCharacterFormProps) {
  const { t } = useTranslation("dashboard");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [voiceStyle, setVoiceStyle] = useState("");
  const [referenceFile, setReferenceFile] = useState<File | null>(null);
  const [referencePreview, setReferencePreview] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const descId = useId();
  const nameRef = useAutoFocus<HTMLInputElement>();

  useEffect(() => {
    return () => {
      if (referencePreview) {
        URL.revokeObjectURL(referencePreview);
      }
    };
  }, [referencePreview]);

  const handleReferenceChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (referencePreview) {
      URL.revokeObjectURL(referencePreview);
    }

    setReferenceFile(file);
    setReferencePreview(URL.createObjectURL(file));
    e.target.value = "";
  };

  const clearReference = () => {
    if (referencePreview) {
      URL.revokeObjectURL(referencePreview);
    }
    setReferenceFile(null);
    setReferencePreview(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !description.trim()) return;
    setSubmitting(true);
    try {
      await onSubmit(
        name.trim(),
        description.trim(),
        voiceStyle.trim(),
        referenceFile,
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="mt-4 rounded-xl border border-indigo-500/30 bg-gray-900 p-4"
      data-workspace-editing="true"
    >
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-200">{t("add_character")}</h3>
        <button
          type="button"
          onClick={onCancel}
          className="rounded p-1 text-gray-400 hover:bg-gray-800 hover:text-gray-200"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <form onSubmit={voidPromise(handleSubmit)} className="space-y-3">
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">
            {t("name_label")} <span className="text-red-400">*</span>
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("name_placeholder")}
            className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-1.5 text-sm text-gray-200 placeholder-gray-500 outline-none focus:border-indigo-500"
            ref={nameRef}
          />
        </div>

        <div>
          <label htmlFor={descId} className="block text-xs font-medium text-gray-400 mb-1">
            {t("desc_label")} <span className="text-red-400">*</span>
          </label>
          <textarea
            id={descId}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={t("char_desc_placeholder")}
            rows={3}
            className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-1.5 text-sm text-gray-200 placeholder-gray-500 outline-none focus:border-indigo-500 resize-none"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">
            {t("voice_style_label")}
          </label>
          <input
            type="text"
            value={voiceStyle}
            onChange={(e) => setVoiceStyle(e.target.value)}
            placeholder={t("voice_style_example")}
            className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-1.5 text-sm text-gray-200 placeholder-gray-500 outline-none focus:border-indigo-500"
          />
        </div>

        <div>
          <div className="mb-1 flex items-center justify-between">
            <label className="block text-xs font-medium text-gray-400">
              {t("reference_image_optional")}
            </label>
            {referenceFile && (
              <span className="text-[11px] text-gray-500">{referenceFile.name}</span>
            )}
          </div>

          {referencePreview ? (
            <div className="relative overflow-hidden rounded-lg border border-gray-700 bg-gray-800">
              <img
                src={referencePreview}
                alt={t("reference_image")}
                className="h-32 w-full object-cover"
              />
              <div className="absolute inset-x-0 bottom-0 flex items-center justify-between bg-gradient-to-t from-black/70 to-transparent px-3 py-2">
                <span className="flex items-center gap-1.5 text-xs text-gray-200">
                  <ImagePlus className="h-3.5 w-3.5" />
                  {t("reference_selected")}
                </span>
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="rounded bg-black/40 px-2 py-1 text-xs text-gray-200 transition-colors hover:bg-black/60"
                  >
                    {t("change")}
                  </button>
                  <button
                    type="button"
                    onClick={clearReference}
                    className="rounded bg-black/40 px-2 py-1 text-xs text-gray-200 transition-colors hover:bg-black/60"
                  >
                    {t("clear")}
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-gray-700 bg-gray-800/50 px-3 py-4 text-sm text-gray-500 transition-colors hover:border-gray-500 hover:text-gray-300"
            >
              <Upload className="h-4 w-4" />
              {t("upload_ref_image")}
            </button>
          )}

          <input
            ref={fileInputRef}
            type="file"
            accept=".png,.jpg,.jpeg,.webp"
            aria-label={t("upload_character_ref_aria")}
            onChange={handleReferenceChange}
            className="hidden"
          />
          <p className="mt-1 text-xs text-gray-600">
            {t("consistency_hint")}
          </p>
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg px-3 py-1.5 text-sm text-gray-400 hover:text-gray-200 transition-colors"
          >
            {t("cancel")}
          </button>
          <button
            type="submit"
            disabled={submitting || !name.trim() || !description.trim()}
            className="rounded-lg bg-indigo-500 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-400 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? (
              <span className="inline-flex items-center gap-1.5">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {t("adding")}
              </span>
            ) : (
              t("add")
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
