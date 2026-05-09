import { useEffect, useRef, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Check, Upload, X } from "lucide-react";
import {
  DEFAULT_TEMPLATE_ID,
  getTemplatesByCategory,
  type StyleCategory,
} from "@/data/style-templates";

export interface StylePickerValue {
  mode: "template" | "custom";
  templateId: string | null;
  activeCategory: "live" | "anim";
  uploadedFile: File | null;
  /** Either a blob: URL (just-uploaded) or a /api/v1/files/... URL (already saved). */
  uploadedPreview: string | null;
}

export interface StylePickerProps {
  value: StylePickerValue;
  onChange: (next: StylePickerValue) => void;
}

const SELECTED_RING_STYLE: CSSProperties = {
  boxShadow:
    "inset 0 0 0 1.5px var(--color-accent), 0 0 0 4px var(--color-bg-grad-a), 0 0 24px -8px var(--color-accent-glow)",
};

const HOVER_RING_STYLE: CSSProperties = {
  boxShadow: "inset 0 0 0 1px var(--color-hairline)",
};

interface TemplateCardProps {
  thumbnail: string;
  label: string;
  tagline: string;
  isSelected: boolean;
  isDefault: boolean;
  defaultLabel: string;
  onClick: () => void;
}

function TemplateCard({
  thumbnail,
  label,
  tagline,
  isSelected,
  isDefault,
  defaultLabel,
  onClick,
}: TemplateCardProps) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={isSelected}
      onClick={onClick}
      className="group relative aspect-[3/4] overflow-hidden rounded-[8px] transition-transform duration-150 motion-safe:hover:-translate-y-px focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      style={isSelected ? SELECTED_RING_STYLE : HOVER_RING_STYLE}
    >
      <img
        src={thumbnail}
        alt={label}
        width={240}
        height={320}
        loading="lazy"
        decoding="async"
        className="h-full w-full object-cover"
        onError={(e) => {
          (e.currentTarget as HTMLImageElement).style.display = "none";
        }}
      />
      {/* Fallback gradient if image errors out */}
      <div
        aria-hidden
        className="-z-10 absolute inset-0"
        style={{
          background:
            "linear-gradient(135deg, oklch(0.30 0.04 295), oklch(0.18 0.012 265))",
        }}
      />

      {/* Bottom label gradient */}
      <div
        className="absolute inset-x-0 bottom-0 px-2 py-1.5"
        style={{
          background:
            "linear-gradient(180deg, transparent 0%, oklch(0 0 0 / 0.8) 100%)",
        }}
      >
        <p className="truncate text-[11px] leading-tight text-text">{label}</p>
        {tagline && (
          <p className="mt-0.5 truncate text-[9px] leading-tight text-text-3">
            {tagline}
          </p>
        )}
      </div>

      {/* Selected check */}
      {isSelected && (
        <div
          className="absolute right-1.5 top-1.5 grid h-5 w-5 place-items-center rounded-full"
          style={{
            background:
              "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
            color: "oklch(0.14 0 0)",
            boxShadow: "0 0 14px -4px var(--color-accent-glow)",
          }}
        >
          <Check size={11} strokeWidth={3} aria-hidden />
        </div>
      )}

      {/* Default tag */}
      {isDefault && (
        <div
          className="absolute left-1.5 top-1.5 rounded-full px-1.5 py-0.5 font-mono text-[8.5px] font-bold uppercase tracking-[0.12em]"
          style={{
            background: "oklch(0 0 0 / 0.55)",
            color: "var(--color-accent-2)",
            border: "1px solid var(--color-accent-soft)",
            backdropFilter: "blur(6px)",
            WebkitBackdropFilter: "blur(6px)",
          }}
        >
          {defaultLabel}
        </div>
      )}
    </button>
  );
}

function revokeBlobUrl(url: string | null) {
  if (url && url.startsWith("blob:")) URL.revokeObjectURL(url);
}

export function StylePicker({ value, onChange }: StylePickerProps) {
  const { t } = useTranslation(["common", "templates"]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const ownedBlobUrlRef = useRef<string | null>(null);

  useEffect(() => {
    return () => {
      revokeBlobUrl(ownedBlobUrlRef.current);
      ownedBlobUrlRef.current = null;
    };
  }, []);

  const handleCustomTab = () => {
    onChange({ ...value, mode: "custom" });
  };

  const handleCategoryTab = (cat: StyleCategory) => {
    onChange({
      ...value,
      mode: "template",
      activeCategory: cat,
    });
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    revokeBlobUrl(ownedBlobUrlRef.current);
    const objectUrl = URL.createObjectURL(file);
    ownedBlobUrlRef.current = objectUrl;
    onChange({
      ...value,
      mode: "custom",
      templateId: null,
      uploadedFile: file,
      uploadedPreview: objectUrl,
    });
    e.target.value = "";
  };

  const handleClearUpload = () => {
    revokeBlobUrl(ownedBlobUrlRef.current);
    ownedBlobUrlRef.current = null;
    onChange({ ...value, uploadedFile: null, uploadedPreview: null });
  };

  const tabCls = (active: boolean) =>
    [
      "rounded-[6px] px-3 py-1 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
      active
        ? "bg-accent-dim text-accent-2"
        : "text-text-3 hover:text-text",
    ].join(" ");

  const isCustomActive = value.mode === "custom";
  const isLiveActive = value.mode === "template" && value.activeCategory === "live";
  const isAnimActive = value.mode === "template" && value.activeCategory === "anim";
  const templates = value.mode === "template" ? getTemplatesByCategory(value.activeCategory) : [];

  return (
    <div className="space-y-4">
      {/* Tab pills */}
      <div className="flex w-fit gap-1 rounded-[8px] border border-hairline bg-bg-grad-a/55 p-1">
        <button
          type="button"
          onClick={handleCustomTab}
          className={tabCls(isCustomActive)}
        >
          {t("templates:category.custom")}
        </button>
        <button
          type="button"
          onClick={() => handleCategoryTab("live")}
          className={tabCls(isLiveActive)}
        >
          {t("templates:category.live")}
        </button>
        <button
          type="button"
          onClick={() => handleCategoryTab("anim")}
          className={tabCls(isAnimActive)}
        >
          {t("templates:category.anim")}
        </button>
      </div>

      {value.mode === "custom" ? (
        <div>
          <p className="mb-3 text-[12.5px] leading-[1.55] text-text-3">
            {t("templates:tab_custom_desc")}
          </p>

          {value.uploadedPreview ? (
            <div className="relative overflow-hidden rounded-[10px] border border-hairline">
              <img
                src={value.uploadedPreview}
                alt={t("templates:upload_reference")}
                className="h-40 w-full object-cover"
              />
              <button
                type="button"
                onClick={handleClearUpload}
                aria-label={t("common:remove")}
                className="absolute right-1.5 top-1.5 rounded-full p-1 text-text-2 transition-colors hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                style={{
                  background: "oklch(0 0 0 / 0.55)",
                  backdropFilter: "blur(6px)",
                  WebkitBackdropFilter: "blur(6px)",
                }}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="flex w-full items-center justify-center gap-2 rounded-[10px] border border-dashed border-hairline-strong bg-bg-grad-a/45 px-3 py-7 text-[12.5px] text-text-3 transition-colors hover:border-accent/45 hover:bg-accent-dim hover:text-accent-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <Upload className="h-3.5 w-3.5" />
              <span>{t("templates:upload_reference")}</span>
            </button>
          )}

          <input
            ref={fileInputRef}
            type="file"
            accept=".png,.jpg,.jpeg,.webp"
            onChange={handleFileChange}
            className="hidden"
          />
          <p className="mt-2 font-mono text-[10px] uppercase tracking-[0.14em] text-text-4">
            {t("templates:supported_formats")}
          </p>
        </div>
      ) : (
        <div className="grid max-h-[420px] grid-cols-4 gap-3 overflow-y-auto p-1 pr-2">
          {templates.map((tpl) => (
            <TemplateCard
              key={tpl.id}
              thumbnail={tpl.thumbnail}
              label={t(`templates:name.${tpl.id}`)}
              tagline={t(`templates:tagline.${tpl.id}`, "")}
              isSelected={value.templateId === tpl.id}
              isDefault={tpl.id === DEFAULT_TEMPLATE_ID}
              defaultLabel={t("templates:template_selected_default")}
              onClick={() => onChange({ ...value, mode: "template", templateId: tpl.id })}
            />
          ))}
        </div>
      )}
    </div>
  );
}
