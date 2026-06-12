import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { ShoppingBag } from "lucide-react";
import { GalleryToolbar } from "./GalleryToolbar";
import { ProductCard } from "./ProductCard";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { useScrollTarget } from "@/hooks/useScrollTarget";
import type { Product } from "@/types";
import { GalleryEmptyState } from "./GalleryEmptyState";

interface Props {
  projectName: string;
  products: Record<string, Product>;
  onUpdateProduct: (name: string, updates: Partial<Product>) => void;
  onGenerateProduct: (name: string) => void;
  onAddProduct: (name: string, description: string, brand: string) => Promise<void>;
  onRestoreProductVersion?: () => Promise<void> | void;
  onRefreshProject?: () => Promise<unknown> | void;
  generatingProductNames?: Set<string>;
}

/**
 * 产品资产页：审核原图与标准参考图、编辑卖点/品牌、触发 sheet 生成与版本回滚。
 * 产品不入全局资产库（多图列表模型），故无「从资产库选择」入口。
 */
export function ProductsPage({
  projectName,
  products,
  onUpdateProduct,
  onGenerateProduct,
  onAddProduct,
  onRestoreProductVersion,
  onRefreshProject,
  generatingProductNames,
}: Props) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const [adding, setAdding] = useState(false);

  useScrollTarget("product");

  const entries = Object.entries(products);

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <GalleryToolbar
        title={t("dashboard:products")}
        count={entries.length}
        onAdd={() => setAdding(true)}
      />
      <div className="px-5 py-5">
        {entries.length === 0 ? (
          <GalleryEmptyState
            icon={<ShoppingBag className="h-6 w-6" />}
            label={t("dashboard:products")}
            hint={t("dashboard:no_products_hint_clickable")}
            onClick={() => setAdding(true)}
          />
        ) : (
          <div className="grid justify-evenly gap-4 [grid-template-columns:repeat(auto-fill,320px)]">
            {entries.map(([name, product]) => (
              <ProductCard
                key={name}
                name={name}
                product={product}
                projectName={projectName}
                onUpdate={onUpdateProduct}
                onGenerate={onGenerateProduct}
                onRestoreVersion={onRestoreProductVersion}
                onReload={onRefreshProject}
                generating={generatingProductNames?.has(name)}
              />
            ))}
          </div>
        )}
      </div>

      {adding && (
        <ProductFormModal
          onClose={() => setAdding(false)}
          onSubmit={async ({ name, description, brand }) => {
            await onAddProduct(name, description, brand);
            setAdding(false);
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ProductFormModal — 产品专用创建表单（名称 + 描述 + 品牌）
// ---------------------------------------------------------------------------

const FIELD_STYLE: React.CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.6), oklch(0.18 0.010 265 / 0.45))",
  border: "1px solid var(--color-hairline)",
  color: "var(--color-text)",
  boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.2)",
};

function ProductFormModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (payload: { name: string; description: string; brand: string }) => Promise<void>;
}) {
  const { t } = useTranslation(["dashboard", "assets", "common"]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [brand, setBrand] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const titleId = useId();
  const nameId = useId();
  const descId = useId();
  const brandId = useId();

  const canSubmit = !submitting && name.trim() !== "";

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      await onSubmit({ name: name.trim(), description: description.trim(), brand: brand.trim() });
    } catch {
      // 失败时保持弹窗打开（toast 由上层回调发出）
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <GlassModal open onClose={onClose} labelledBy={titleId}>
      <div className="p-5">
        <div className="mb-4 flex items-center gap-2.5">
          <ShoppingBag className="h-4 w-4" style={{ color: "var(--color-accent-2)" }} />
          <h2
            id={titleId}
            className="display-serif flex-1 text-[16px] font-semibold tracking-tight"
            style={{ color: "var(--color-text)" }}
          >
            {t("dashboard:add_product")}
          </h2>
          <ModalCloseButton onClick={onClose} />
        </div>

        <div className="space-y-3">
          <div>
            <label
              htmlFor={nameId}
              className="text-[10px] font-semibold uppercase tracking-[0.12em]"
              style={{ color: "var(--color-text-4)" }}
            >
              {t("dashboard:ad_init_product_name_label")}
            </label>
            <input
              id={nameId}
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={submitting}
              className="focus-ring mt-1.5 w-full rounded-lg px-3 py-2 text-[13px] outline-none"
              style={FIELD_STYLE}
            />
          </div>
          <div>
            <label
              htmlFor={descId}
              className="text-[10px] font-semibold uppercase tracking-[0.12em]"
              style={{ color: "var(--color-text-4)" }}
            >
              {t("dashboard:description")}
            </label>
            <textarea
              id={descId}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={submitting}
              rows={3}
              className="focus-ring mt-1.5 w-full resize-y rounded-lg px-3 py-2 text-[13px] leading-[1.6] outline-none"
              style={FIELD_STYLE}
              placeholder={t("dashboard:product_desc_placeholder")}
            />
          </div>
          <div>
            <label
              htmlFor={brandId}
              className="text-[10px] font-semibold uppercase tracking-[0.12em]"
              style={{ color: "var(--color-text-4)" }}
            >
              {t("dashboard:product_brand_label")}
            </label>
            <input
              id={brandId}
              type="text"
              value={brand}
              onChange={(e) => setBrand(e.target.value)}
              disabled={submitting}
              className="focus-ring mt-1.5 w-full rounded-lg px-3 py-2 text-[13px] outline-none"
              style={FIELD_STYLE}
              placeholder={t("dashboard:product_brand_placeholder")}
            />
          </div>
        </div>

        <div className="mt-5 flex items-center justify-end gap-2">
          <SecondaryButton onClick={onClose} disabled={submitting}>
            {t("common:cancel")}
          </SecondaryButton>
          <PrimaryButton onClick={() => void handleSubmit()} disabled={!canSubmit}>
            {submitting ? t("common:saving") : t("common:save")}
          </PrimaryButton>
        </div>
      </div>
    </GlassModal>
  );
}
