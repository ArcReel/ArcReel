import { TruncatedText } from "@/components/shared/TruncatedText";
import { useLayoutEffect, useRef, useState } from "react";
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
  /** 待上传的参考图；有文件时选择器按文件自建预览，不读 `uploadedPreview`。 */
  uploadedFile: File | null;
  /** 已保存的参考图地址（/api/v1/files/...）；选择器不往这里写 blob: 地址。 */
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
        <TruncatedText text={label} focusable={false} className="text-xs font-medium text-foreground" />
        {tagline && <TruncatedText text={tagline} focusable={false} className="text-xs text-subtle-foreground" />}
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

/**
 * 待上传文件的预览地址，只由选择器持有：随文件创建，换文件或卸载时收回；重新挂载时按文件重建，
 * 不会把已收回的地址留在调用方的取值里。layout effect 在绘制前换上地址，预览不闪一帧空态。
 */
function useFilePreview(file: File | null): string | null {
  const [preview, setPreview] = useState<{ file: File; url: string } | null>(null);
  useLayoutEffect(() => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- object URL 随文件创建并在换文件或卸载时回收（同 AssetCreateDialog）
    setPreview({ file, url });
    return () => URL.revokeObjectURL(url);
  }, [file]);
  return preview?.file === file ? preview.url : null;
}

/**
 * 风格选择：风格模版（真人、动画两类）或上传一张风格参考图。
 * 模版网格不自带滚动，由所在的弹层或页面负责滚动，避免嵌套滚动区。
 */
export function StylePicker({ value, onChange }: StylePickerProps) {
  const { t } = useTranslation(["common", "templates"]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const filePreview = useFilePreview(value.uploadedFile);
  const preview = value.uploadedFile ? filePreview : value.uploadedPreview;

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
    onChange({
      ...value,
      mode: "custom",
      templateId: null,
      uploadedFile: file,
      uploadedPreview: null,
    });
    e.target.value = "";
  };

  const handleClearUpload = () => {
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
          {preview ? (
            <div className="relative overflow-hidden rounded-lg border border-border">
              <img src={preview} alt={t("templates:upload_reference")} className="h-48 w-full object-cover" />
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
