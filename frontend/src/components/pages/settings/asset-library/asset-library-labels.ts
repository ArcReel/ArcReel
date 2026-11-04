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

/**
 * ArcReel 自己判定的入库失败码（``lib.asset_library.ingest.LOCAL_FAILURE_CODES``），界面按码给出本地化原因；
 * 其余失败码是供应商原文。两端经 `test/contracts/provider-asset-local-failures.json` 核对。
 */
export const PROVIDER_ASSET_LOCAL_FAILURES: ReadonlySet<string> = new Set([
  "unlinked_before_submit",
  "account_missing",
  "account_scope_changed",
  "object_storage_missing",
  "content_unavailable",
  "content_changed",
  "relay_upload_failed",
]);
