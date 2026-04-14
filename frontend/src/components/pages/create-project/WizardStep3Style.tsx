import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { Check, Upload, X, Loader2 } from "lucide-react";
import {
  STYLE_TEMPLATES,
  DEFAULT_TEMPLATE_ID,
  getTemplatesByCategory,
  type StyleCategory,
} from "@/data/style-templates";

// ─── Public API ─────────────────────────────────────────────────────────────

export interface WizardStep3Value {
  mode: "template" | "custom";
  templateId: string | null;
  activeCategory: "live" | "anim";
  uploadedFile: File | null;
  uploadedPreview: string | null;
}

export interface WizardStep3StyleProps {
  value: WizardStep3Value;
  onChange: (next: WizardStep3Value) => void;
  onBack: () => void;
  onCreate: () => void;
  onCancel: () => void;
  creating: boolean;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Fallback id when switching categories without a preserved selection */
function fallbackIdForCategory(cat: StyleCategory): string {
  if (cat === "live") return DEFAULT_TEMPLATE_ID;
  const t = STYLE_TEMPLATES.find((x) => x.category === cat);
  return t ? t.id : DEFAULT_TEMPLATE_ID;
}

/** True when id belongs to the given category */
function idBelongsToCategory(id: string | null, cat: StyleCategory): boolean {
  if (!id) return false;
  return STYLE_TEMPLATES.some((t) => t.id === id && t.category === cat);
}

// ─── Sub-component: template card ────────────────────────────────────────────

interface TemplateCardProps {
  id: string;
  thumbnail: string;
  label: string;
  tagline: string;
  isSelected: boolean;
  isDefault: boolean;
  defaultLabel: string;
  onClick: () => void;
}

function TemplateCard({
  id,
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
      className={[
        "aspect-[3/4] relative rounded-lg overflow-hidden transition-all duration-150",
        isSelected
          ? "ring-2 ring-indigo-500 ring-offset-2 ring-offset-gray-950"
          : "ring-1 ring-gray-800 hover:ring-gray-600",
      ].join(" ")}
    >
      <img
        src={thumbnail}
        alt={label}
        className="w-full h-full object-cover"
        onError={(e) => {
          // Show a placeholder gradient when thumbnail is missing
          (e.currentTarget as HTMLImageElement).style.display = "none";
        }}
      />
      {/* Fallback gradient background (shown when img fails to load) */}
      <div className="absolute inset-0 bg-gradient-to-br from-gray-800 to-gray-900 -z-10" />

      {/* Bottom gradient overlay + name */}
      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent px-2 py-1.5">
        <p className="text-[11px] text-white leading-tight truncate">{label}</p>
        {tagline && (
          <p className="text-[9px] text-gray-400 leading-tight truncate mt-0.5">{tagline}</p>
        )}
      </div>

      {/* Selected checkmark badge */}
      {isSelected && (
        <div className="absolute top-1.5 right-1.5 w-5 h-5 rounded-full bg-indigo-500 text-white flex items-center justify-center shadow">
          <Check size={12} strokeWidth={3} />
        </div>
      )}

      {/* Default badge */}
      {isDefault && (
        <div className="absolute top-1.5 left-1.5 px-1.5 py-0.5 text-[9px] rounded-full bg-indigo-500/25 backdrop-blur text-indigo-200 leading-tight">
          {defaultLabel}
        </div>
      )}
    </button>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────

export function WizardStep3Style({
  value,
  onChange,
  onBack,
  onCreate,
  onCancel,
  creating,
}: WizardStep3StyleProps) {
  const { t } = useTranslation(["common", "dashboard", "templates"]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── Tab handlers ────────────────────────────────────────────────────────────

  const handleCustomTab = () => {
    onChange({ ...value, mode: "custom", templateId: null });
  };

  const handleCategoryTab = (cat: StyleCategory) => {
    const keepId = idBelongsToCategory(value.templateId, cat)
      ? value.templateId
      : fallbackIdForCategory(cat);
    onChange({
      ...value,
      mode: "template",
      activeCategory: cat,
      templateId: keepId,
    });
  };

  // ── Upload handlers ─────────────────────────────────────────────────────────

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const objectUrl = URL.createObjectURL(file);
    onChange({ ...value, mode: "custom", templateId: null, uploadedFile: file, uploadedPreview: objectUrl });
    // Reset so same file can be re-selected
    e.target.value = "";
  };

  const handleClearUpload = () => {
    if (value.uploadedPreview) {
      URL.revokeObjectURL(value.uploadedPreview);
    }
    onChange({ ...value, uploadedFile: null, uploadedPreview: null });
  };

  // ── Create button disabled state ────────────────────────────────────────────

  const isCreateDisabled =
    creating || (value.mode === "custom" && value.uploadedFile === null);

  // ── Tab button style helper ──────────────────────────────────────────────────

  const tabCls = (active: boolean) =>
    [
      "rounded-md px-3 py-1 text-xs transition-colors",
      active
        ? "bg-indigo-500/15 text-indigo-300 font-medium"
        : "text-gray-400 hover:text-gray-200",
    ].join(" ");

  const isCustomActive = value.mode === "custom";
  const isLiveActive = value.mode === "template" && value.activeCategory === "live";
  const isAnimActive = value.mode === "template" && value.activeCategory === "anim";

  // ── Template grid ────────────────────────────────────────────────────────────

  const templates = value.mode === "template" ? getTemplatesByCategory(value.activeCategory) : [];

  return (
    <div className="space-y-4">
      {/* Tab bar */}
      <div className="rounded-lg bg-gray-900 border border-gray-800 p-1 flex gap-1 w-fit">
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

      {/* Panel content */}
      {value.mode === "custom" ? (
        <div>
          <p className="text-sm text-gray-400 mb-3">{t("templates:tab_custom_desc")}</p>

          {value.uploadedPreview ? (
            <div className="relative rounded-lg border border-gray-700 overflow-hidden">
              <img
                src={value.uploadedPreview}
                alt={t("templates:upload_reference")}
                className="w-full h-40 object-cover"
              />
              <button
                type="button"
                onClick={handleClearUpload}
                aria-label={t("common:remove")}
                className="absolute top-1.5 right-1.5 rounded-full bg-gray-900/80 p-1 text-gray-300 hover:bg-gray-900 hover:text-white transition-colors"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-gray-700 bg-gray-800/50 px-3 py-6 text-sm text-gray-500 transition-colors hover:border-gray-500 hover:text-gray-300"
            >
              <Upload className="h-4 w-4" />
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
          <p className="mt-1.5 text-xs text-gray-600">{t("templates:supported_formats")}</p>
        </div>
      ) : (
        <div className="grid grid-cols-4 gap-3 max-h-[420px] overflow-y-auto pr-1">
          {templates.map((tpl) => (
            <TemplateCard
              key={tpl.id}
              id={tpl.id}
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

      {/* Footer */}
      <div className="flex items-center justify-between mt-6 pt-4 border-t border-gray-800">
        <button
          type="button"
          onClick={onCancel}
          className="text-sm text-gray-400 hover:text-gray-200 transition-colors"
        >
          {t("common:cancel")}
        </button>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onBack}
            className="rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-300 hover:bg-gray-800 transition-colors"
          >
            {t("templates:prev_step")}
          </button>
          <button
            type="button"
            onClick={onCreate}
            disabled={isCreateDisabled}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {creating ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                {t("dashboard:creating")}
              </>
            ) : (
              t("dashboard:create_project")
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
