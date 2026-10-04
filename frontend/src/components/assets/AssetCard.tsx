import { TruncatedText } from "@/components/shared/TruncatedText";
import { memo, useId } from "react";
import { useTranslation } from "react-i18next";
import { FolderInput, Layers, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Asset } from "@/types/asset";
import { ASSET_TYPE_ICON } from "./asset-type-icons";
import { AssetThumb } from "./AssetThumb";

export type AssetCardAction = "open" | "edit" | "apply" | "delete";

/**
 * 资产库卡片：整张卡片可点，打开详情；「更多」菜单常驻，不依赖悬停，提供编辑、应用到项目与删除。
 */
export const AssetCard = memo(function AssetCard({
  asset,
  onAction,
}: {
  asset: Asset;
  /** 需传稳定引用。 */
  onAction: (action: AssetCardAction, asset: Asset) => void;
}) {
  const { t } = useTranslation("assets");
  const titleId = useId();
  const Icon = ASSET_TYPE_ICON[asset.type];
  const derivativeCount = asset.derivatives.length;

  return (
    <article
      aria-labelledby={titleId}
      className="relative flex min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-border bg-card transition-colors duration-fast hover:border-input"
    >
      <AssetThumb
        imageUrl={API.getGlobalAssetUrl(asset.image_path, asset.updated_at)}
        alt=""
        fallback={<Icon aria-hidden className="size-8" />}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-1 p-3 pt-2.5">
        <div className="flex min-w-0 items-center gap-1">
          <h3 id={titleId} className="min-w-0 flex-1 text-sm font-medium">
            {/* 名称按钮的伪元素铺满整张卡片，点卡片任意处都打开详情；「更多」在 DOM 中靠后、自身定位，盖在它上面。 */}
            <button
              type="button"
              onClick={() => onAction("open", asset)}
              aria-label={asset.name}
              className="block w-full text-left outline-none after:absolute after:inset-0 after:rounded-lg focus-visible:after:ring-3 focus-visible:after:ring-ring/50"
            >
              <TruncatedText text={asset.name} focusable={false} />
            </button>
          </h3>
          <DropdownMenu>
            <DropdownMenuTrigger
              render={<Button variant="ghost" size="icon-sm" className="relative -mr-1.5 shrink-0" />}
              aria-label={t("asset_menu_label", { name: asset.name })}
            >
              <MoreHorizontal aria-hidden />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuGroup>
                <DropdownMenuItem onClick={() => onAction("edit", asset)}>
                  <Pencil aria-hidden />
                  {t("edit")}
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => onAction("apply", asset)}>
                  <FolderInput aria-hidden />
                  {t("apply_to_project")}
                </DropdownMenuItem>
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => onAction("delete", asset)}>
                <Trash2 aria-hidden />
                {t("delete")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        <p className="line-clamp-2 text-sm text-subtle-foreground">
          {asset.description || <span className="text-muted-foreground">{t("no_description")}</span>}
        </p>
        {derivativeCount > 0 && (
          <p className="mt-auto flex items-center gap-1 pt-1 text-xs text-muted-foreground">
            <Layers aria-hidden className="size-3.5" />
            {t("derivatives_count", { count: derivativeCount })}
          </p>
        )}
      </div>
    </article>
  );
});
