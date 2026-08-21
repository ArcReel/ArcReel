import { useEffect, useId, useState, useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  AudioLines,
  Check,
  ChevronDown,
  Image as ImageIcon,
  ImagePlus,
  Landmark,
  Package,
  Pause,
  Play,
  User,
} from "lucide-react";
import { API } from "@/api";
import type { Asset, AssetResource, AssetType } from "@/types/asset";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
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
    primary_image_resource_id?: string;
    primary_audio_resource_id?: string;
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
  const resources = initialData?.resources ?? [];
  const imageResources = resources.filter((resource) => resource.media_type === "image");
  const audioResources = resources.filter((resource) => resource.media_type === "audio");
  const [primaryImageResourceId, setPrimaryImageResourceId] = useState(
    imageResources.find((resource) => resource.is_primary)?.id ?? imageResources[0]?.id ?? "",
  );
  const [primaryAudioResourceId, setPrimaryAudioResourceId] = useState(
    audioResources.find((resource) => resource.is_primary)?.id ?? audioResources[0]?.id ?? "",
  );
  const selectedImageResource = imageResources.find((resource) => resource.id === primaryImageResourceId);
  const fileRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const titleId = useId();

  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!image) {
      // image 变更时同步重置本地预览（动作驱动重置）
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLocalPreview(null);
      return;
    }
    const url = URL.createObjectURL(image);
    setLocalPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [image]);

  const selectedResourcePreview = selectedImageResource
    ? API.getGlobalAssetUrl(selectedImageResource.path, initialData?.updated_at)
    : null;
  const displayedPreview = sanitizeImageSrc(localPreview ?? selectedResourcePreview ?? previewImageUrl);
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
      await onSubmit({
        name: name.trim(),
        description,
        voice_style: voiceStyle,
        image,
        overwrite,
        primary_image_resource_id: primaryImageResourceId || undefined,
        primary_audio_resource_id: primaryAudioResourceId || undefined,
      });
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
          style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
        >
          <span
            aria-hidden
            className="grid h-9 w-9 shrink-0 place-items-center rounded-lg"
            style={{
              background:
                "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 160 / 0.05))",
              border: "1px solid var(--color-accent-soft)",
              color: "var(--color-accent-2)",
              boxShadow: "0 8px 18px -8px var(--color-accent-glow)",
            }}
          >
            <TypeIcon className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h3
              id={titleId}
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
          <ModalCloseButton onClick={onClose} />
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
                    className="absolute inset-0 h-full w-full object-contain"
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
                        "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 160 / 0.05))",
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

            {isCharacter && mode === "edit" && initialData?.voice_id && (
              <FieldGroup label={t("field.voice_id")}>
                <div
                  className="select-text break-all rounded-lg px-3 py-2 font-mono text-[11px]"
                  style={{
                    background: "oklch(0.16 0.010 265 / 0.45)",
                    border: "1px solid var(--color-hairline-soft)",
                    color: "var(--color-text-3)",
                  }}
                >
                  {initialData.voice_id}
                </div>
              </FieldGroup>
            )}

            {isCharacter && mode === "edit" && imageResources.length > 1 && (
              <FieldGroup label={t("field.primary_image")}>
                <ImageResourcePicker
                  resources={imageResources}
                  value={primaryImageResourceId}
                  fingerprint={initialData?.updated_at}
                  onChange={setPrimaryImageResourceId}
                />
              </FieldGroup>
            )}

            {isCharacter && mode === "edit" && audioResources.length > 0 && (
              <FieldGroup label={t("field.primary_audio")}>
                <AudioResourcePicker
                  resources={audioResources}
                  value={primaryAudioResourceId}
                  fingerprint={initialData?.updated_at}
                  onChange={setPrimaryAudioResourceId}
                />
              </FieldGroup>
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
          <SecondaryButton size="sm" onClick={onClose}>
            {t("cancel")}
          </SecondaryButton>
          {mode === "import" && conflictWith && (
            <PrimaryButton
              size="sm"
              tone="warm"
              onClick={() => void submit(true)}
              disabled={submitting}
            >
              {t("overwrite_existing")}
            </PrimaryButton>
          )}
          <PrimaryButton
            size="sm"
            className="ml-auto"
            onClick={() => void submit(false)}
            disabled={submitting || !name.trim()}
          >
            {primaryLabel}
          </PrimaryButton>
        </div>
    </GlassModal>
  );
}

function ImageResourcePicker({
  resources,
  value,
  fingerprint,
  onChange,
}: {
  resources: AssetResource[];
  value: string;
  fingerprint?: string | null;
  onChange: (resourceId: string) => void;
}) {
  const { t } = useTranslation("assets");
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listboxId = useId();
  const selectedIndex = Math.max(0, resources.findIndex((resource) => resource.id === value));
  const selected = resources[selectedIndex];

  useEffect(() => {
    if (!open) return;
    const closeOnOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  const labelFor = (resource: AssetResource, index: number) => {
    const knownLabels: Record<string, string> = {
      avatarUrl: t("resource_image_avatar"),
      fullBodyImageUrl: t("resource_image_full_body"),
      halfBodyImageUrl: t("resource_image_half_body"),
      chestImageUrl: t("resource_image_chest"),
    };
    return knownLabels[resource.key] ?? t("resource_image_option", { index: index + 1 });
  };

  const thumbnail = (resource: AssetResource, label: string) => {
    const src = sanitizeImageSrc(API.getGlobalAssetUrl(resource.path, fingerprint));
    return (
      <span
        className="relative grid h-10 w-14 shrink-0 place-items-center overflow-hidden rounded-md"
        style={{ background: "oklch(0.13 0.008 265)", border: "1px solid var(--color-hairline-soft)" }}
      >
        <ImageIcon aria-hidden className="h-4 w-4 text-text-4" />
        {src && (
          <img
            src={src}
            alt={label}
            className="absolute inset-0 h-full w-full object-cover"
            onError={(event) => { event.currentTarget.style.display = "none"; }}
          />
        )}
      </span>
    );
  };

  if (!selected) return null;
  const selectedLabel = labelFor(selected, selectedIndex);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        onClick={() => setOpen((current) => !current)}
        className="focus-ring flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left outline-none"
        style={{
          background: "oklch(0.16 0.010 265 / 0.6)",
          border: "1px solid var(--color-hairline)",
          color: "var(--color-text)",
        }}
      >
        {thumbnail(selected, selectedLabel)}
        <span className="min-w-0 flex-1 truncate text-[12px]">{selectedLabel}</span>
        <ChevronDown aria-hidden className={`h-4 w-4 shrink-0 text-text-4 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div
          id={listboxId}
          role="listbox"
          aria-label={t("field.primary_image")}
          className="absolute inset-x-0 top-full z-20 mt-1 max-h-64 overflow-y-auto rounded-lg border p-1 shadow-2xl"
          style={{ background: "oklch(0.17 0.010 265)", borderColor: "var(--color-hairline)" }}
        >
          {resources.map((resource, index) => {
            const label = labelFor(resource, index);
            const selectedOption = resource.id === value;
            return (
              <button
                key={resource.id}
                type="button"
                role="option"
                aria-selected={selectedOption}
                onClick={() => {
                  onChange(resource.id);
                  setOpen(false);
                }}
                className="focus-ring flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left outline-none transition-colors hover:bg-white/5"
              >
                {thumbnail(resource, label)}
                <span className="min-w-0 flex-1 truncate text-[12px] text-text-2">{label}</span>
                {selectedOption && <Check aria-hidden className="h-4 w-4 shrink-0 text-accent" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function AudioResourcePicker({
  resources,
  value,
  fingerprint,
  onChange,
}: {
  resources: AssetResource[];
  value: string;
  fingerprint?: string | null;
  onChange: (resourceId: string) => void;
}) {
  const { t } = useTranslation("assets");
  const [open, setOpen] = useState(false);
  const [playingResourceId, setPlayingResourceId] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const panelId = useId();
  const selectedIndex = Math.max(0, resources.findIndex((resource) => resource.id === value));
  const selected = resources[selectedIndex];

  useEffect(() => {
    if (!open) return;
    const closeOnOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  useEffect(() => () => audioRef.current?.pause(), []);

  const labelFor = (_resource: AssetResource, index: number) => (
    t("resource_audio_option", { index: index + 1 })
  );

  const urlFor = (resource: AssetResource) => (
    API.getGlobalAssetUrl(resource.path, fingerprint)
  );

  const togglePreview = (resource: AssetResource) => {
    const audio = audioRef.current;
    const url = urlFor(resource);
    if (!audio || !url) return;

    if (playingResourceId === resource.id) {
      audio.pause();
      setPlayingResourceId(null);
      return;
    }

    audio.src = url;
    audio.currentTime = 0;
    setPlayingResourceId(resource.id);
    void audio.play().catch(() => setPlayingResourceId(null));
  };

  const previewButton = (resource: AssetResource, label: string) => {
    const playing = playingResourceId === resource.id;
    const previewLabel = playing
      ? t("pause_audio_preview", { label })
      : t("play_audio_preview", { label });
    return (
      <button
        type="button"
        aria-label={previewLabel}
        title={previewLabel}
        disabled={!urlFor(resource)}
        onClick={() => togglePreview(resource)}
        className="focus-ring grid h-9 w-9 shrink-0 place-items-center rounded-md outline-none transition-colors hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40"
        style={{ color: playing ? "var(--color-accent-2)" : "var(--color-text-3)" }}
      >
        {playing
          ? <Pause aria-hidden className="h-4 w-4 fill-current" />
          : <Play aria-hidden className="h-4 w-4 fill-current" />}
      </button>
    );
  };

  if (!selected) return null;
  const selectedLabel = labelFor(selected, selectedIndex);

  return (
    <div ref={rootRef} className="relative">
      {/* Catalog voice samples do not include caption tracks. */}
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <audio
        ref={audioRef}
        className="hidden"
        preload="none"
        onEnded={() => setPlayingResourceId(null)}
        onError={() => setPlayingResourceId(null)}
      />
      <div
        className="flex w-full items-center gap-1 rounded-lg p-1"
        style={{
          background: "oklch(0.16 0.010 265 / 0.6)",
          border: "1px solid var(--color-hairline)",
          color: "var(--color-text)",
        }}
      >
        {previewButton(selected, selectedLabel)}
        <button
          type="button"
          aria-label={t("select_primary_audio", { label: selectedLabel })}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={open ? panelId : undefined}
          onClick={() => setOpen((current) => !current)}
          className="focus-ring flex min-w-0 flex-1 items-center gap-2 px-1.5 py-1 text-left outline-none"
        >
          <AudioLines aria-hidden className="h-4 w-4 shrink-0 text-text-4" />
          <span className="min-w-0 flex-1 truncate text-[12px]">{selectedLabel}</span>
          <ChevronDown aria-hidden className={`h-4 w-4 shrink-0 text-text-4 transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
      </div>
      {open && (
        <div
          id={panelId}
          role="dialog"
          aria-label={t("field.primary_audio")}
          className="absolute inset-x-0 top-full z-20 mt-1 max-h-64 overflow-y-auto rounded-lg border p-1 shadow-2xl"
          style={{ background: "oklch(0.17 0.010 265)", borderColor: "var(--color-hairline)" }}
        >
          {resources.map((resource, index) => {
            const label = labelFor(resource, index);
            const selectedOption = resource.id === value;
            return (
              <div key={resource.id} className="flex items-center gap-1 rounded-md hover:bg-white/5">
                {previewButton(resource, label)}
                <button
                  type="button"
                  aria-pressed={selectedOption}
                  onClick={() => {
                    onChange(resource.id);
                    setOpen(false);
                  }}
                  className="focus-ring flex min-w-0 flex-1 items-center gap-2 rounded-md px-1.5 py-2 text-left outline-none"
                >
                  <span className="min-w-0 flex-1 truncate text-[12px] text-text-2">{label}</span>
                  {selectedOption && <Check aria-hidden className="h-4 w-4 shrink-0 text-accent" />}
                </button>
              </div>
            );
          })}
        </div>
      )}
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

function FieldGroup({ label, children }: { label: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span
        className="num text-[10px] uppercase"
        style={{ color: "var(--color-text-4)", letterSpacing: "1.0px" }}
      >
        {label}
      </span>
      {children}
    </div>
  );
}
