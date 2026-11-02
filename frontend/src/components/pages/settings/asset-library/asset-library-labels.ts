import type { AssetLibrarySite, ProviderAssetStatus } from "@/types";

const SITE_LABEL_KEYS: Record<AssetLibrarySite, string> = {
  volcengine: "dashboard:asset_library_site_volcengine",
  byteplus: "dashboard:asset_library_site_byteplus",
};

/** 站点的显示名翻译 key。 */
export function siteLabelKey(site: AssetLibrarySite): string {
  return SITE_LABEL_KEYS[site];
}

const STATUS_LABEL_KEYS: Record<ProviderAssetStatus, string> = {
  active: "assets:provider_asset_status_active",
  processing: "assets:provider_asset_status_processing",
  failed: "assets:provider_asset_status_failed",
  unknown: "assets:provider_asset_status_unknown",
};

/** 供应商素材状态的显示名翻译 key。 */
export function providerAssetStatusLabelKey(status: ProviderAssetStatus): string {
  return STATUS_LABEL_KEYS[status];
}
