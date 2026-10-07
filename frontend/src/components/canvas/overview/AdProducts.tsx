import { useCallback, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, ShoppingBag } from "lucide-react";
import { Link } from "wouter";

import { enqueueProduct } from "@/actions/generation";
import { API } from "@/api";
import { WORKSPACE_ROUTE_PRODUCTS } from "@/app-routes";
import { AssetEditorSheet, type AssetEditorTarget } from "@/components/canvas/lorebook/AssetEditorSheet";
import { useAssetSheetStatus, useSheetStatusByName } from "@/components/canvas/lorebook/useAssetSheetStatus";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button, buttonVariants } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useActiveResourceIds } from "@/stores/tasks-store";
import type { Product } from "@/types";
import { errMsg } from "@/utils/async";

const NO_PRODUCTS: Record<string, Product> = {};

/**
 * 广告项目概览常驻的「商品」区：列出已登记的商品，点一行或「添加商品」从右侧打开资产详情 Sheet。
 * 没有商品时显示空状态；没有商品也能只凭创作灵感生成脚本。
 */
export function AdProducts({
  projectName,
  products = NO_PRODUCTS,
  readOnly,
}: {
  projectName: string;
  products: Record<string, Product> | undefined;
  readOnly: boolean;
}) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const headingId = useId();
  const [target, setTarget] = useState<AssetEditorTarget | null>(null);
  const rows = useAssetSheetStatus(projectName);
  const statusByName = useSheetStatusByName(rows, "product");
  const generatingNames = useActiveResourceIds("product", projectName);

  const generate = useCallback(
    async (name: string) => {
      try {
        await enqueueProduct(projectName, name);
      } catch (err) {
        useAppStore.getState().pushToast(t("dashboard:submit_failed", { message: errMsg(err) }), "error");
      }
    },
    [projectName, t],
  );
  const add = readOnly ? undefined : () => setTarget({ mode: "create" });
  const entries = Object.entries(products);

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <div className="flex min-h-7 items-center gap-2">
        <h2 id={headingId} className="flex-1 text-sm font-medium text-subtle-foreground">
          {t("dashboard:products")}
        </h2>
        {entries.length > 0 ? (
          <>
            <Link href={`/${WORKSPACE_ROUTE_PRODUCTS}`} className={buttonVariants({ variant: "ghost", size: "sm" })}>
              {t("dashboard:ad_products_view_all")}
            </Link>
            {add ? (
              <Button variant="ghost" size="sm" onClick={add}>
                <Plus aria-hidden data-icon="inline-start" />
                {t("assets:gallery_add.product")}
              </Button>
            ) : null}
          </>
        ) : null}
      </div>
      {entries.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border">
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <ShoppingBag aria-hidden />
              </EmptyMedia>
              <EmptyTitle>{t("assets:gallery_empty_title.product")}</EmptyTitle>
              {add ? <EmptyDescription>{t("dashboard:ad_products_empty_hint")}</EmptyDescription> : null}
            </EmptyHeader>
            {add ? (
              <EmptyContent>
                <Button variant="outline" onClick={add}>
                  <Plus aria-hidden data-icon="inline-start" />
                  {t("assets:gallery_add.product")}
                </Button>
              </EmptyContent>
            ) : null}
          </Empty>
        </div>
      ) : (
        <ul className="-mx-2 flex flex-col">
          {entries.map(([name, product]) => (
            <li key={name}>
              <ProductRow
                projectName={projectName}
                name={name}
                product={product}
                onOpen={() => setTarget({ mode: "edit", name })}
              />
            </li>
          ))}
        </ul>
      )}
      <AssetEditorSheet
        projectName={projectName}
        assetType="product"
        assets={products}
        target={target}
        onTargetChange={setTarget}
        statusByName={statusByName}
        generatingNames={generatingNames}
        readOnly={readOnly}
        onGenerate={generate}
      />
    </section>
  );
}

/** 一个商品一行：资产图（没有时用第一张原图）、名称与描述，整行打开详情。 */
function ProductRow({
  projectName,
  name,
  product,
  onOpen,
}: {
  projectName: string;
  name: string;
  product: Product;
  onOpen: () => void;
}) {
  const imagePath = product.product_sheet || product.reference_images?.[0] || null;
  const fingerprint = useProjectsStore((s) => (imagePath ? s.getAssetFingerprint(imagePath) : null));
  const imageUrl = imagePath ? API.getFileUrl(projectName, imagePath, fingerprint, { width: 160 }) : null;
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const description = product.description.trim();

  return (
    <button
      type="button"
      onClick={onOpen}
      className="focus-ring flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left transition-colors duration-fast hover:bg-muted/50"
    >
      <span className="flex size-10 shrink-0 items-center justify-center overflow-hidden rounded-md bg-muted text-muted-foreground">
        {imageUrl && imageUrl !== failedUrl ? (
          <img
            src={imageUrl}
            alt=""
            loading="lazy"
            decoding="async"
            className="size-full object-cover"
            onError={() => setFailedUrl(imageUrl)}
          />
        ) : (
          <ShoppingBag aria-hidden className="size-4" />
        )}
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <TruncatedText text={name} focusable={false} className="text-sm text-foreground" />
        {description ? <span className="truncate text-sm text-muted-foreground">{description}</span> : null}
      </span>
    </button>
  );
}
