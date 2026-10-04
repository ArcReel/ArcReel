import { useEffect, useId, useState, useRef } from "react";
import { useTranslation } from "react-i18next";
import { ImagePlus, Landmark, Package, User } from "lucide-react";
import type { AssetType } from "@/types/asset";
import { GlassModal } from "@/components/legacy/GlassModal";
import { ModalCloseButton } from "@/components/legacy/ModalCloseButton";
import { PrimaryButton } from "@/components/legacy/PrimaryButton";
import { SecondaryButton } from "@/components/legacy/SecondaryButton";
import { sanitizeImageSrc } from "@/utils/safe-url";

interface Props {
  type: AssetType;
  onClose: () => void;
  onSubmit: (payload: {
    name: string;
    description: string;
    voice_style: string;
    image?: File | null;
  }) => Promise<void>;
}

const TYPE_ICON: Record<AssetType, React.ComponentType<{ className?: string }>> = {
  character: User,
  scene: Landmark,
  prop: Package,
};

/** 项目画廊里新增角色、场景或道具的表单弹窗。 */
export function AssetFormModal({ type, onClose, onSubmit }: Props) {
  const { t } = useTranslation("assets");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [voiceStyle, setVoiceStyle] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [localPreview, setLocalPreview] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const titleId = useId();

  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!image) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- image 变更时同步重置本地预览，是动作驱动重置
      setLocalPreview(null);
      return;
    }
    const url = URL.createObjectURL(image);
    setLocalPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [image]);

  const displayedPreview = sanitizeImageSrc(localPreview);
  const TypeIcon = TYPE_ICON[type];

  const isCharacter = type === "character";
  const typeLabel = t(`type.${type}`);
  const title = t("create_title", { type: typeLabel });

  const submit = async () => {
    setSubmitting(true);
    try {
      await onSubmit({ name: name.trim(), description, voice_style: voiceStyle, image });
      onClose();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <GlassModal
      open
      onClose={onClose}
      labelledBy={titleId}
      widthClassName="w-[580px] max-w-[96vw]"
    >
      {/* Header */}
        <div
          className="flex items-start gap-3 px-6 py-5"
          style={{ borderBottom: "1px solid color-mix(in oklab, var(--border) 50%, transparent)" }}
        >
          <span
            aria-hidden
            className="grid h-9 w-9 shrink-0 place-items-center rounded-lg"
            style={{
              background:
                "linear-gradient(135deg, color-mix(in oklab, var(--primary) 12%, transparent), oklch(0.76 0.09 295 / 0.05))",
              border: "1px solid color-mix(in oklab, var(--primary) 22%, transparent)",
              color: "var(--primary)",
              boxShadow: "0 8px 18px -8px color-mix(in oklab, var(--primary) 35%, transparent)",
            }}
          >
            <TypeIcon className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h3
              id={titleId}
              className="display-serif truncate text-[15px] font-semibold tracking-tight"
              style={{ color: "var(--foreground)" }}
            >
              {title}
            </h3>
            <p
              className="num mt-0.5 text-[10px] uppercase"
              style={{
                color: "var(--muted-foreground)",
                letterSpacing: "1.0px",
              }}
            >
              {typeLabel}
            </p>
          </div>
          <ModalCloseButton onClick={onClose} />
        </div>


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
                border: "1px dashed var(--border)",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = "color-mix(in oklab, var(--primary) 22%, transparent)";
                e.currentTarget.style.borderStyle = "dashed";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = "var(--border)";
              }}
            >
              {displayedPreview ? (
                <>
                  <img
                    src={displayedPreview}
                    alt=""
                    className="absolute inset-0 h-full w-full object-contain"
                  />
                  <div
                    className="absolute inset-0 flex items-center justify-center gap-2 text-[13px] opacity-0 transition-opacity group-hover:opacity-100"
                    style={{
                      background: "oklch(0 0 0 / 0.6)",
                      color: "var(--foreground)",
                    }}
                  >
                    <ImagePlus className="h-4 w-4" />
                    {t("replace_image")}
                  </div>
                </>
              ) : (
                <div
                  className="flex h-full w-full flex-col items-center justify-center gap-2 px-4 text-center transition-colors"
                  style={{ color: "var(--muted-foreground)" }}
                >
                  <span
                    aria-hidden
                    className="grid h-10 w-10 place-items-center rounded-full"
                    style={{
                      background:
                        "linear-gradient(135deg, color-mix(in oklab, var(--primary) 12%, transparent), oklch(0.76 0.09 295 / 0.05))",
                      border: "1px solid color-mix(in oklab, var(--primary) 22%, transparent)",
                      color: "var(--primary)",
                    }}
                  >
                    <ImagePlus className="h-4 w-4" />
                  </span>
                  <span
                    className="text-[12px]"
                    style={{ color: "var(--muted-foreground)" }}
                  >
                    {t("upload_image_hint")}
                  </span>
                  <span
                    className="text-[10px]"
                    style={{ color: "var(--muted-foreground)" }}
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
                  <span style={{ color: "var(--primary)" }}>*</span>
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
                  border: "1px solid var(--border)",
                  color: "var(--foreground)",
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
                  border: "1px solid var(--border)",
                  color: "var(--foreground)",
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
                    border: "1px solid var(--border)",
                    color: "var(--foreground)",
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
            borderTop: "1px solid color-mix(in oklab, var(--border) 50%, transparent)",
            background: "oklch(0.17 0.010 250 / 0.5)",
          }}
        >
          <SecondaryButton size="sm" onClick={onClose}>
            {t("cancel")}
          </SecondaryButton>
          <PrimaryButton
            size="sm"
            className="ml-auto"
            onClick={() => void submit()}
            disabled={submitting || !name.trim()}
          >
            {t("create")}
          </PrimaryButton>
        </div>
    </GlassModal>
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
          color: "var(--muted-foreground)",
          letterSpacing: "1.0px",
        }}
      >
        {label}
      </span>
      {children}
    </label>
  );
}
