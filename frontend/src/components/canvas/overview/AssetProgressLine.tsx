import { Fragment } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";

import {
  WORKSPACE_ROUTE_CHARACTERS,
  WORKSPACE_ROUTE_PRODUCTS,
  WORKSPACE_ROUTE_PROPS,
  WORKSPACE_ROUTE_SCENES,
} from "@/app-routes";
import type { ProjectData } from "@/types";

const ASSET_KINDS = [
  { key: "character", labelKey: "overview_asset_character", route: WORKSPACE_ROUTE_CHARACTERS },
  { key: "scene", labelKey: "overview_asset_scene", route: WORKSPACE_ROUTE_SCENES },
  { key: "prop", labelKey: "overview_asset_prop", route: WORKSPACE_ROUTE_PROPS },
  { key: "product", labelKey: "overview_asset_product", route: WORKSPACE_ROUTE_PRODUCTS },
] as const;

/** 资产图完成度一行：每类「已有 / 总数」链接到对应画廊，有过期时追加过期数。商品只在广告项目出现。 */
export function AssetProgressLine({ data }: { data: ProjectData }) {
  const { t } = useTranslation("dashboard");
  const assets = data.status?.assets;
  if (!assets) return null;
  const kinds = ASSET_KINDS.filter((kind) => kind.key !== "product" || data.content_mode === "ad");

  return (
    <div className="flex flex-wrap items-baseline gap-x-1 gap-y-1 text-sm">
      <span className="mr-2 text-muted-foreground">{t("overview_assets_label")}</span>
      {kinds.map((kind, index) => {
        const count = assets[kind.key] ?? { total: 0, available: 0, stale: 0 };
        return (
          <Fragment key={kind.key}>
            {index > 0 ? (
              <span aria-hidden className="text-muted-foreground">
                ·
              </span>
            ) : null}
            <Link
              href={`/${kind.route}`}
              className="focus-ring rounded-sm px-1 text-subtle-foreground hover:text-foreground hover:underline"
            >
              {t(kind.labelKey)} <span className="num">{count.available}/{count.total}</span>
              {count.stale > 0 ? (
                <>
                  {" "}
                  <span className="text-warn">{t("overview_asset_stale", { count: count.stale })}</span>
                </>
              ) : null}
            </Link>
          </Fragment>
        );
      })}
    </div>
  );
}
