import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Product } from "@/types";
import { AssetGallery } from "./AssetGallery";
import { ProductCard } from "./ProductCard";
import { ProductCreateDialog } from "./ProductCreateDialog";

interface Props {
  projectName: string;
  products: Record<string, Product>;
  onUpdateProduct: (name: string, updates: Partial<Product>) => void;
  onGenerateProduct: (name: string) => void;
  onAddProduct: (name: string, description: string, brand: string) => Promise<void>;
  onRestoreProductVersion?: () => Promise<void> | void;
  onRefreshProject?: () => Promise<unknown> | void;
  generatingProductNames?: Set<string>;
  /** 只读展示（引导演示项目）：不渲染新增、生成、上传入口。 */
  readOnly?: boolean;
}

/** 商品画廊。商品不入全局资产库（多图列表模型），没有「从资产库选择」「加入资产库」与「并入…」。 */
export function ProductsPage({
  projectName,
  products,
  onUpdateProduct,
  onGenerateProduct,
  onAddProduct,
  onRestoreProductVersion,
  onRefreshProject,
  generatingProductNames,
  readOnly = false,
}: Props) {
  const { t } = useTranslation("dashboard");
  const [adding, setAdding] = useState(false);

  return (
    <>
      <AssetGallery
        projectName={projectName}
        assetType="product"
        title={t("products")}
        assets={products}
        generatingNames={generatingProductNames}
        readOnly={readOnly}
        onGenerate={onGenerateProduct}
        onRestoreVersion={onRestoreProductVersion}
        onReload={onRefreshProject}
        onAdd={() => setAdding(true)}
        renderEditor={(name, { sheetStatus, generating }) =>
          products[name] ? (
            <ProductCard
              name={name}
              product={products[name]}
              projectName={projectName}
              onUpdate={onUpdateProduct}
              onGenerate={onGenerateProduct}
              onReload={onRefreshProject}
              generating={generating}
              sheetStatus={sheetStatus}
              readOnly={readOnly}
            />
          ) : null
        }
      />

      {adding && !readOnly && (
        <ProductCreateDialog
          onClose={() => setAdding(false)}
          onSubmit={async ({ name, description, brand }) => {
            await onAddProduct(name, description, brand);
            setAdding(false);
          }}
        />
      )}
    </>
  );
}
