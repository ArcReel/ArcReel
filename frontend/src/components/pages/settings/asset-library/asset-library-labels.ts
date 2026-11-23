import type { AssetLibrarySite, ProviderAssetStatus } from "@/types";

const SITE_LABEL_KEYS: Record<AssetLibrarySite, string> = {
  volcengine: "dashboard:asset_library_site_volcengine",
  byteplus: "dashboard:asset_library_site_byteplus",
};

export function siteLabelKey(site: AssetLibrarySite): string {
  return SITE_LABEL_KEYS[site];
}

const STATUS_LABEL_KEYS: Record<ProviderAssetStatus, string> = {
  active: "assets:provider_asset_status_active",
  processing: "assets:provider_asset_status_processing",
  failed: "assets:provider_asset_status_failed",
  unknown: "assets:provider_asset_status_unknown",
};

export function providerAssetStatusLabelKey(status: ProviderAssetStatus): string {
  return STATUS_LABEL_KEYS[status];
}

export const PROVIDER_ASSET_LOCAL_FAILURES: ReadonlySet<string> = new Set([
  "unlinked_before_submit",
  "account_missing",
  "account_scope_changed",
  "object_storage_missing",
  "object_storage_changed",
  "content_unavailable",
  "content_changed",
  "relay_upload_failed",
]);
