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
  /** 只读展示（引导演示项目）：不渲染新增、生成、上传入口。 */
  readOnly?: boolean;
}

/** 商品画廊。商品不入全局资产库（多图列表模型），没有「从资产库选择」「加入资产库」与「并入…」。 */
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
