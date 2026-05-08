import { useEffect, useState, useRef } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ImagePlus, Landmark, Package, User, X } from "lucide-react";
import type { Asset, AssetType } from "@/types/asset";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { sanitizeImageSrc } from "@/utils/safe-url";
import { WARM_TONE } from "@/utils/severity-tone";

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

const PANEL_BG =
  "linear-gradient(180deg, oklch(0.21 0.012 265 / 0.96), oklch(0.18 0.010 265 / 0.96))";

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
        className="absolute inset-0"
        style={{
          background: "oklch(0 0 0 / 0.65)",
          backdropFilter: "blur(2px)",
          WebkitBackdropFilter: "blur(2px)",
        }}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="relative w-[580px] max-w-[96vw] overflow-hidden rounded-2xl"
        style={{
          background: PANEL_BG,
          border: "1px solid var(--color-hairline)",
          backdropFilter: "blur(14px)",
          WebkitBackdropFilter: "blur(14px)",
          boxShadow:
            "0 24px 60px -12px oklch(0 0 0 / 0.6), inset 0 1px 0 oklch(1 0 0 / 0.05)",
        }}
      >
        <span
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-px"
          style={{
            background:
              "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)",
          }}
        />

        {/* Header */}
        <div
          className="flex items-start gap-3 px-6 py-5"
          style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
        >
          <span
            aria-hidden
            className="grid h-9 w-9 shrink-0 place-items-center rounded-lg"
            style={{
              background:
                "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.05))",
              border: "1px solid var(--color-accent-soft)",
              color: "var(--color-accent-2)",
              boxShadow: "0 8px 18px -8px var(--color-accent-glow)",
            }}
          >
            <TypeIcon className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h3
              className="display-serif truncate text-[15px] font-semibold tracking-tight"
              style={{ color: "var(--color-text)" }}
            >
              {title}
            </h3>
            <p
              className="num mt-0.5 text-[10px] uppercase"
              style={{
                color: "var(--color-text-4)",
                letterSpacing: "1.0px",
              }}
            >
              {mode === "import" ? t("library_subtitle") : typeLabel}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("close")}
            className="focus-ring grid h-7 w-7 place-items-center rounded-md transition-colors"
            style={{ color: "var(--color-text-3)" }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = "var(--color-text)";
              e.currentTarget.style.background = "oklch(1 0 0 / 0.05)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = "var(--color-text-3)";
              e.currentTarget.style.background = "transparent";
            }}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Conflict warning */}
        {conflictWith && (
          <div
            className="flex items-start gap-2 px-6 py-3 text-[12px]"
            style={{
              borderBottom: `1px solid ${WARM_TONE.ring}`,
              background: WARM_TONE.soft,
              color: WARM_TONE.color,
            }}
          >
            <AlertTriangle className="h-4 w-4 shrink-0" />
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
              className="focus-ring group relative aspect-video w-full overflow-hidden rounded-xl transition-colors"
              style={{
                background: "oklch(0.16 0.010 265 / 0.6)",
                border: "1px dashed var(--color-hairline)",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = "var(--color-accent-soft)";
                e.currentTarget.style.borderStyle = "dashed";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = "var(--color-hairline)";
              }}
            >
              {displayedPreview ? (
                <>
                  <img
                    src={displayedPreview}
                    alt=""
                    className="absolute inset-0 h-full w-full object-cover"
                  />
                  <div
                    className="absolute inset-0 flex items-center justify-center gap-2 text-[13px] opacity-0 transition-opacity group-hover:opacity-100"
                    style={{
                      background: "oklch(0 0 0 / 0.6)",
                      color: "var(--color-text)",
                    }}
                  >
                    <ImagePlus className="h-4 w-4" />
                    {t("replace_image")}
                  </div>
                </>
              ) : (
                <div
                  className="flex h-full w-full flex-col items-center justify-center gap-2 px-4 text-center transition-colors"
                  style={{ color: "var(--color-text-4)" }}
                >
                  <span
                    aria-hidden
                    className="grid h-10 w-10 place-items-center rounded-full"
                    style={{
                      background:
                        "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.05))",
                      border: "1px solid var(--color-accent-soft)",
                      color: "var(--color-accent-2)",
                    }}
                  >
                    <ImagePlus className="h-4 w-4" />
                  </span>
                  <span
                    className="text-[12px]"
                    style={{ color: "var(--color-text-3)" }}
                  >
                    {t("upload_image_hint")}
                  </span>
                  <span
                    className="text-[10px]"
                    style={{ color: "var(--color-text-4)" }}
                  >
                    {t("upload_image_optional")}
                  </span>
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
            <FieldLabel
              label={
                <>
                  {t("field.name")}{" "}
                  <span style={{ color: "var(--color-accent-2)" }}>*</span>
                </>
              }
            >
              <input
                ref={nameRef}
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="focus-ring rounded-lg px-3 py-2 text-[13px] outline-none"
                style={{
                  background: "oklch(0.16 0.010 265 / 0.6)",
                  border: "1px solid var(--color-hairline)",
                  color: "var(--color-text)",
                }}
              />
            </FieldLabel>

            <FieldLabel label={t("field.description")}>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={4}
                className="focus-ring resize-none rounded-lg px-3 py-2 text-[13px] leading-[1.55] outline-none"
                style={{
                  background: "oklch(0.16 0.010 265 / 0.6)",
                  border: "1px solid var(--color-hairline)",
                  color: "var(--color-text)",
                }}
              />
            </FieldLabel>

            {isCharacter && (
              <FieldLabel label={t("field.voice_style")}>
                <input
                  value={voiceStyle}
                  onChange={(e) => setVoiceStyle(e.target.value)}
                  className="focus-ring rounded-lg px-3 py-2 text-[13px] outline-none"
                  style={{
                    background: "oklch(0.16 0.010 265 / 0.6)",
                    border: "1px solid var(--color-hairline)",
                    color: "var(--color-text)",
                  }}
                />
              </FieldLabel>
            )}
          </div>
        </div>

        {/* Footer */}
        <div
          className="flex items-center gap-2 px-6 py-4"
          style={{
            borderTop: "1px solid var(--color-hairline-soft)",
            background: "oklch(0.17 0.010 250 / 0.5)",
          }}
        >
          <button
            type="button"
            onClick={onClose}
            className="focus-ring rounded-md px-3.5 py-1.5 text-[12.5px] transition-colors"
            style={{
              color: "var(--color-text-3)",
              border: "1px solid var(--color-hairline)",
              background: "oklch(0.22 0.011 265 / 0.5)",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = "var(--color-text)";
              e.currentTarget.style.background = "oklch(0.26 0.013 265 / 0.7)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = "var(--color-text-3)";
              e.currentTarget.style.background = "oklch(0.22 0.011 265 / 0.5)";
            }}
          >
            {t("cancel")}
          </button>
          {mode === "import" && conflictWith && (
            <button
              type="button"
              onClick={() => void submit(true)}
              disabled={submitting}
              className="focus-ring rounded-md px-3.5 py-1.5 text-[12.5px] transition-colors disabled:opacity-50"
              style={{
                color: WARM_TONE.color,
                border: `1px solid ${WARM_TONE.ring}`,
                background: WARM_TONE.soft,
              }}
            >
              {t("overwrite_existing")}
            </button>
          )}
          <button
            type="button"
            onClick={() => void submit(false)}
            disabled={submitting || !name.trim()}
            className="focus-ring ml-auto rounded-md px-4 py-1.5 text-[12.5px] font-medium transition-transform disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              color: "oklch(0.14 0 0)",
              background:
                "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
              boxShadow:
                "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -6px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
            }}
            onMouseEnter={(e) => {
              if (!e.currentTarget.disabled)
                e.currentTarget.style.transform = "translateY(-1px)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.transform = "translateY(0)";
            }}
          >
            {primaryLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

function FieldLabel({
  label,
  children,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span
        className="num text-[10px] uppercase"
        style={{
          color: "var(--color-text-4)",
          letterSpacing: "1.0px",
        }}
      >
        {label}
      </span>
      {children}
    </label>
  );
}
