import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Upload, X } from "lucide-react";
import { cn } from "cn";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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

type StyleTab = "custom" | StyleCategory;

interface TemplateCardProps {
  thumbnail: string;
  label: string;
  tagline: string;
  isSelected: boolean;
  isDefault: boolean;
  defaultLabel: string;
  onClick: () => void;
}

function TemplateCard({ thumbnail, label, tagline, isSelected, isDefault, defaultLabel, onClick }: TemplateCardProps) {
  const [broken, setBroken] = useState(false);
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={isSelected}
      onClick={onClick}
      className={cn(
        "group relative aspect-3/4 overflow-hidden rounded-lg bg-muted ring-1 transition-shadow focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
        isSelected ? "ring-2 ring-primary" : "ring-border hover:ring-input",
      )}
    >
      {!broken && (
        <img
          src={thumbnail}
          alt=""
          width={240}
          height={320}
          loading="lazy"
          decoding="async"
          className="size-full object-cover"
          onError={() => setBroken(true)}
        />
      )}
      <div className="absolute inset-x-0 bottom-0 bg-linear-to-b from-transparent to-background/90 px-2 pt-6 pb-1.5 text-left">
        <p className="truncate text-xs font-medium text-foreground">{label}</p>
        {tagline && <p className="truncate text-xs text-subtle-foreground">{tagline}</p>}
      </div>
      {isSelected && (
        <span className="absolute top-1.5 right-1.5 grid size-5 place-items-center rounded-full bg-primary text-primary-foreground">
          <Check className="size-3" strokeWidth={3} aria-hidden />
        </span>
      )}
      {isDefault && (
        <Badge variant="secondary" className="absolute top-1.5 left-1.5">
          {defaultLabel}
        </Badge>
      )}
    </button>
  );
}

function revokeBlobUrl(url: string | null) {
  if (url && url.startsWith("blob:")) URL.revokeObjectURL(url);
}

/**
 * 风格选择：风格模版（真人、动画两类）或上传一张风格参考图。
 * 模版网格不自带滚动，由所在的弹层或页面负责滚动，避免嵌套滚动区。
 */
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

  const activeTab: StyleTab = value.mode === "custom" ? "custom" : value.activeCategory;

  const handleTabChange = (tab: StyleTab) => {
    if (tab === "custom") {
      onChange({ ...value, mode: "custom" });
    } else {
      onChange({ ...value, mode: "template", activeCategory: tab });
    }
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

  const templateGrid = (category: StyleCategory) => (
    <div className="grid grid-cols-4 gap-3">
      {getTemplatesByCategory(category).map((tpl) => (
        <TemplateCard
          key={tpl.id}
          thumbnail={tpl.thumbnail}
          label={t(`templates:name.${tpl.id}`)}
          tagline={t(`templates:tagline.${tpl.id}`, "")}
          isSelected={value.templateId === tpl.id}
          isDefault={tpl.id === DEFAULT_TEMPLATE_ID}
          defaultLabel={t("templates:template_default_badge")}
          onClick={() => onChange({ ...value, mode: "template", templateId: tpl.id })}
        />
      ))}
    </div>
  );

  return (
    <Tabs value={activeTab} onValueChange={(tab: StyleTab) => handleTabChange(tab)}>
      <TabsList>
        <TabsTrigger value="live">{t("templates:category.live")}</TabsTrigger>
        <TabsTrigger value="anim">{t("templates:category.anim")}</TabsTrigger>
        <TabsTrigger value="custom">{t("templates:category.custom")}</TabsTrigger>
      </TabsList>

      <TabsContent value="live" className="mt-2">{templateGrid("live")}</TabsContent>
      <TabsContent value="anim" className="mt-2">{templateGrid("anim")}</TabsContent>
      <TabsContent value="custom" className="mt-2">
        <div className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">{t("templates:tab_custom_desc")}</p>
          {value.uploadedPreview ? (
            <div className="relative overflow-hidden rounded-lg border border-border">
              <img src={value.uploadedPreview} alt={t("templates:upload_reference")} className="h-48 w-full object-cover" />
              <div className="absolute top-2 right-2">
                <Button variant="secondary" size="icon-sm" onClick={handleClearUpload} aria-label={t("common:remove")}>
                  <X />
                </Button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-input px-3 py-8 text-sm text-muted-foreground transition-colors hover:border-primary/50 hover:bg-primary/10 hover:text-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              <Upload className="size-4" aria-hidden />
              {t("templates:upload_reference")}
            </button>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept=".png,.jpg,.jpeg,.webp"
            onChange={handleFileChange}
            className="hidden"
          />
          <p className="text-xs text-muted-foreground">{t("templates:supported_formats")}</p>
        </div>
      </TabsContent>
    </Tabs>
  );
}
