import { useTranslation } from "react-i18next";
import type { Product } from "@/types";
import { AssetGallery } from "./AssetGallery";

interface Props {
  projectName: string;
  products: Record<string, Product>;
  onGenerateProduct: (name: string) => void;
  onRestoreProductVersion?: () => Promise<unknown> | void;
  onRefreshProject?: () => Promise<unknown> | void;
  generatingProductNames?: Set<string>;
  /** Read-only onboarding demo: omit add, generate and upload actions. */
  readOnly?: boolean;
}

/** Product gallery: products use a multiple-image model outside the global library, without choose-from-library, add-to-library or merge actions. */
export function ProductsPage({
  projectName,
  products,
  onGenerateProduct,
  onRestoreProductVersion,
  onRefreshProject,
  generatingProductNames,
  readOnly = false,
}: Props) {
  const { t } = useTranslation("dashboard");
  return (
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
    />
  );
}
