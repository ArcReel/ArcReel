import { useState, useRef, useEffect, useCallback, useId } from "react";
import { useTranslation } from "react-i18next";
import { ImagePlus, ShoppingBag, Upload } from "lucide-react";
import { API } from "@/api";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import type { Product } from "@/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ProductCardProps {
  name: string;
  product: Product;
  projectName: string;
  onUpdate: (name: string, updates: Partial<Product>) => void;
  onGenerate: (name: string) => void;
  onRestoreVersion?: () => void | Promise<void>;
  onReload?: () => void | Promise<unknown>;
  generating?: boolean;
}

const FIELD_STYLE: React.CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.6), oklch(0.18 0.010 265 / 0.45))",
  border: "1px solid var(--color-hairline)",
  color: "var(--color-text)",
  boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.2)",
};

// ---------------------------------------------------------------------------
// ProductCard — 原图（保真锚点）+ 标准参考图（可生成/重生成/版本回滚）+ 卖点/品牌
// ---------------------------------------------------------------------------

export function ProductCard({
  name,
  product,
  projectName,
  onUpdate,
  onGenerate,
  onRestoreVersion,
  onReload,
  generating = false,
}: ProductCardProps) {
  const { t } = useTranslation(["dashboard", "assets", "common"]);
  const sheetFp = useProjectsStore(
    (s) => product.product_sheet ? s.getAssetFingerprint(product.product_sheet) : null,
  );
  const [description, setDescription] = useState(product.description);
  const [brand, setBrand] = useState(product.brand ?? "");
  const [sellingPointsText, setSellingPointsText] = useState(
    (product.selling_points ?? []).join("\n"),
  );
  const [imgError, setImgError] = useState(false);
  const [uploadingSheet, setUploadingSheet] = useState(false);
  const [uploadingRefs, setUploadingRefs] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const sheetInputRef = useRef<HTMLInputElement>(null);
  const refsInputRef = useRef<HTMLInputElement>(null);

  const referenceImages = product.reference_images ?? [];

  const handleSheetUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploadingSheet(true);
    try {
      await API.uploadFile(projectName, "product", file, name);
      await onReload?.();
      useAppStore.getState().pushToast(t("assets:upload_sheet_success", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setUploadingSheet(false);
    }
  };

  const handleRefsUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (files.length === 0) return;
    setUploadingRefs(true);
    try {
      for (const file of files) {
        await API.uploadFile(projectName, "product_ref", file, name);
      }
      await onReload?.();
      useAppStore.getState().pushToast(t("dashboard:product_ref_uploaded_toast"), "success");
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setUploadingRefs(false);
    }
  };

  const parsedSellingPoints = sellingPointsText
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const isDirty =
    description !== product.description ||
    brand !== (product.brand ?? "") ||
    parsedSellingPoints.join("\n") !== (product.selling_points ?? []).join("\n");

  useEffect(() => {
    // 上游产品数据变化时同步本地草稿
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDescription(product.description);
  }, [product.description]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setBrand(product.brand ?? "");
  }, [product.brand]);

  const sellingPointsKey = (product.selling_points ?? []).join("\n");
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSellingPointsText(sellingPointsKey);
  }, [sellingPointsKey]);

  useEffect(() => {
    // 参考图变化时重置图片加载错误标记
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setImgError(false);
  }, [product.product_sheet, sheetFp]);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const descId = useId();
  const brandId = useId();
  const sellingPointsId = useId();

  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    }
  }, []);

  useEffect(() => {
    autoResize();
  }, [description, autoResize]);

  const handleSave = () => {
    onUpdate(name, {
      description,
      brand,
      selling_points: parsedSellingPoints,
    });
  };

  const sheetUrl = product.product_sheet
    ? API.getFileUrl(projectName, product.product_sheet, sheetFp)
    : null;

  return (
    <div
      id={`product-${name}`}
      className="relative overflow-hidden rounded-xl p-5"
      data-workspace-editing={isEditing || isDirty ? "true" : undefined}
      onFocusCapture={() => setIsEditing(true)}
      onBlurCapture={(event) => {
        const nextTarget = event.relatedTarget;
        if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) {
          return;
        }
        setIsEditing(false);
      }}
      style={{
        background:
          "linear-gradient(180deg, oklch(0.22 0.012 265 / 0.55), oklch(0.19 0.010 265 / 0.40))",
        border: "1px solid var(--color-hairline-soft)",
        boxShadow:
          "inset 0 1px 0 oklch(1 0 0 / 0.04), 0 12px 30px -12px oklch(0 0 0 / 0.4)",
      }}
    >
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-5 top-0 h-px"
        style={{
          background:
            "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)",
        }}
      />

      {/* ---- Header ---- */}
      <div className="mb-4 flex items-center gap-2.5">
        <span
          aria-hidden
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md"
          style={{
            background: "var(--color-accent-dim)",
            border: "1px solid var(--color-accent-soft)",
            color: "var(--color-accent-2)",
          }}
        >
          <ShoppingBag className="h-3.5 w-3.5" />
        </span>
        <h3
          className="display-serif min-w-0 flex-1 truncate text-[16px] font-semibold tracking-tight"
          style={{ color: "var(--color-text)" }}
        >
          {name}
        </h3>
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            onClick={() => sheetInputRef.current?.click()}
            disabled={uploadingSheet}
            title={t("assets:upload_sheet")}
            aria-label={t("assets:upload_sheet")}
            className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:opacity-40"
            style={{ color: "var(--color-text-3)" }}
          >
            <Upload className="h-3.5 w-3.5" />
          </button>
          <input
            ref={sheetInputRef}
            type="file"
            accept=".png,.jpg,.jpeg,.webp"
            aria-label={t("assets:upload_sheet")}
            className="hidden"
            onChange={(e) => void handleSheetUpload(e)}
          />
          <VersionTimeMachine
            projectName={projectName}
            resourceType="products"
            resourceId={name}
            onRestore={onRestoreVersion}
            iconOnly
          />
        </div>
      </div>

      {/* ---- 原图（保真锚点） ---- */}
      <div className="mb-4">
        <div className="flex items-center gap-2">
          <CapsLabel>{t("dashboard:product_reference_images")}</CapsLabel>
          <div className="flex-1" />
          <button
            type="button"
            onClick={() => refsInputRef.current?.click()}
            disabled={uploadingRefs}
            title={t("dashboard:product_upload_refs")}
            aria-label={t("dashboard:product_upload_refs")}
            className="focus-ring inline-flex h-6 w-6 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:opacity-40"
            style={{ color: "var(--color-text-3)" }}
          >
            <ImagePlus className="h-3.5 w-3.5" />
          </button>
          <input
            ref={refsInputRef}
            type="file"
            accept=".png,.jpg,.jpeg,.webp"
            multiple
            aria-label={t("dashboard:product_upload_refs")}
            className="hidden"
            onChange={(e) => void handleRefsUpload(e)}
          />
        </div>
        {referenceImages.length > 0 ? (
          <div className="mt-1.5 flex flex-wrap gap-2">
            {referenceImages.map((ref) => {
              const url = API.getFileUrl(projectName, ref);
              return (
                <div
                  key={ref}
                  className="h-16 w-16 overflow-hidden rounded-md"
                  style={{ border: "1px solid var(--color-hairline-soft)" }}
                >
                  <PreviewableImageFrame src={url} alt={ref}>
                    <img src={url} alt={ref} className="h-full w-full object-cover" />
                  </PreviewableImageFrame>
                </div>
              );
            })}
          </div>
        ) : (
          <button
            type="button"
            onClick={() => refsInputRef.current?.click()}
            disabled={uploadingRefs}
            className="focus-ring mt-1.5 flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-[var(--color-hairline)] px-3 py-3 text-[12px] transition-colors hover:border-[var(--color-accent-soft)]"
            style={{ color: "var(--color-text-4)" }}
          >
            <ImagePlus className="h-3.5 w-3.5" />
            {t("dashboard:product_upload_refs")}
          </button>
        )}
      </div>

      {/* ---- 标准参考图 ---- */}
      <div className="mb-4">
        <CapsLabel>{t("dashboard:product_design")}</CapsLabel>
        <div
          className="mt-1.5 overflow-hidden rounded-lg"
          style={{ border: "1px solid var(--color-hairline-soft)" }}
        >
          <PreviewableImageFrame
            src={sheetUrl && !imgError ? sheetUrl : null}
            alt={`${name} ${t("dashboard:product_design")}`}
          >
            <AspectFrame ratio="16:9">
              {sheetUrl && !imgError ? (
                <img
                  src={sheetUrl}
                  alt={`${name} ${t("dashboard:product_design")}`}
                  className="h-full w-full object-cover"
                  onError={() => setImgError(true)}
                />
              ) : (
                <div
                  className="flex h-full w-full flex-col items-center justify-center gap-2"
                  style={{ color: "var(--color-text-4)" }}
                >
                  <ShoppingBag className="h-10 w-10" />
                  <span className="text-xs">{t("dashboard:click_to_generate")}</span>
                </div>
              )}
            </AspectFrame>
          </PreviewableImageFrame>
        </div>
      </div>

      {/* ---- Description ---- */}
      <CapsLabel htmlFor={descId}>{t("dashboard:description")}</CapsLabel>
      <textarea
        ref={textareaRef}
        id={descId}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        onInput={autoResize}
        rows={2}
        className="focus-ring mt-1.5 mb-3 w-full resize-none overflow-hidden rounded-lg px-3 py-2 text-[13px] leading-[1.55] outline-none transition-[border-color,box-shadow]"
        style={FIELD_STYLE}
        placeholder={t("dashboard:product_desc_placeholder")}
      />

      {/* ---- Brand ---- */}
      <CapsLabel htmlFor={brandId}>{t("dashboard:product_brand_label")}</CapsLabel>
      <input
        id={brandId}
        type="text"
        value={brand}
        onChange={(e) => setBrand(e.target.value)}
        className="focus-ring mt-1.5 mb-3 w-full rounded-lg px-3 py-2 text-[13px] outline-none"
        style={FIELD_STYLE}
        placeholder={t("dashboard:product_brand_placeholder")}
      />

      {/* ---- Selling points ---- */}
      <CapsLabel htmlFor={sellingPointsId}>{t("dashboard:product_selling_points_label")}</CapsLabel>
      <textarea
        id={sellingPointsId}
        value={sellingPointsText}
        onChange={(e) => setSellingPointsText(e.target.value)}
        rows={3}
        className="focus-ring mt-1.5 mb-3 w-full resize-y rounded-lg px-3 py-2 text-[13px] leading-[1.55] outline-none"
        style={FIELD_STYLE}
        placeholder={t("dashboard:product_selling_points_placeholder")}
      />

      {isDirty && (
        <button
          type="button"
          onClick={handleSave}
          className="focus-ring mb-3 inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium transition-transform"
          style={{
            color: "oklch(0.14 0 0)",
            background:
              "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
            boxShadow:
              "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
          }}
        >
          {t("common:save")}
        </button>
      )}

      <GenerateButton
        onClick={() => onGenerate(name)}
        loading={generating}
        label={product.product_sheet ? t("dashboard:regenerate_design") : t("dashboard:generate_design")}
        className="w-full justify-center"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small utilities
// ---------------------------------------------------------------------------

function CapsLabel({
  children,
  htmlFor,
}: {
  children: React.ReactNode;
  htmlFor?: string;
}) {
  return (
    <label
      htmlFor={htmlFor}
      className="text-[10px] font-semibold uppercase tracking-[0.12em]"
      style={{ color: "var(--color-text-4)" }}
    >
      {children}
    </label>
  );
}
