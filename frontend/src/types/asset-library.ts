export interface ObjectStorageConnection {
  kind: "tos";
  region: string;
  endpoint: string;
  bucket: string;
  access_key_id_masked: string;
  secret_access_key_masked: string;
  updated_at: string;
}

export type NewImageTarget = "local" | "object_storage";

export interface ObjectStorageSettings {
  connection: ObjectStorageConnection | null;
  new_image_target: NewImageTarget;
  pending_ingest_tasks: number;
}

export interface ObjectStorageCleanupResult {
  cleanup_failures: Record<string, number>;
}

export type SavedObjectStorageConnection = ObjectStorageConnection & ObjectStorageCleanupResult;

export interface SaveObjectStorageConnectionPayload {
  region: string;
  endpoint: string;
  bucket: string;
  access_key_id?: string;
  secret_access_key?: string;
  delete_remote_objects?: boolean;
}

export type AssetLibrarySite = "volcengine" | "byteplus";

export interface AssetLibrarySiteInfo {
  id: AssetLibrarySite;
  regions: string[];
}

export interface AssetLibraryAccount {
  id: number;
  site: AssetLibrarySite;
  name: string;
  region: string;
  project_name: string;
  access_key_id_masked: string;
  secret_access_key_masked: string;
  created_at: string;
}

export interface AssetLibraryAccountList {
  sites: AssetLibrarySiteInfo[];
  default_project_name: string;
  accounts: AssetLibraryAccount[];
}

export interface CreateAssetLibraryAccountPayload {
  site: AssetLibrarySite;
  name: string;
  project_name: string;
  access_key_id: string;
  secret_access_key: string;
}

export interface UpdateAssetLibraryAccountPayload {
  site?: AssetLibrarySite;
  name?: string;
  project_name?: string;
  access_key_id?: string;
  secret_access_key?: string;
}

export type AssetLibraryCredentialType = "builtin" | "custom";
export type AssetLibraryBindingStatus = "confirmed" | "needs_confirmation";

export interface AssetLibraryBindableCredential {
  credential_type: AssetLibraryCredentialType;
  credential_id: number;
  provider: string;
  name: string;
  api_key_masked: string | null;
  endpoint: string;
  binding: { account_id: number; status: AssetLibraryBindingStatus } | null;
}

export type ProviderAssetStatus = "active" | "processing" | "failed" | "unknown";

export interface ProviderAsset {
  id: string;
  name: string;
  asset_type: string;
  status: ProviderAssetStatus;
  error_code: string | null;
  created_at: string | null;
}

export interface ProviderAssetPage {
  items: ProviderAsset[];
  total: number;
  page: number;
  page_size: number;
}

export type ProviderAssetLinkState = "pending" | "submitting" | "processing" | "active" | "failed" | "uncertain";

export interface ProviderAssetLink {
  id: number;
  content_version_id: string;
  version: number;
  account_id: number;
  scope_key: string;
  site: AssetLibrarySite;
  region: string;
  provider_project_name: string;
  state: ProviderAssetLinkState;
  selected: boolean;
  usable: boolean;
  provider_asset_id: string | null;
  error_code: string | null;
  error_message: string | null;
  check_error: string | null;
  polling: boolean;
  created_at: string | null;
  submitted_at: string | null;
  settled_at: string | null;
}

export interface ProviderAssetLinkAccount {
  id: number;
  scope_key: string;
  name: string;
  site: AssetLibrarySite;
  region: string;
  project_name: string;
}

export interface ProviderAssetLinkPanel {
  content_version: { id: string | null; version: number; digest: string | null } | null;
  object_storage_configured: boolean;
  accounts: ProviderAssetLinkAccount[];
  links: ProviderAssetLink[];
}

export interface ProviderAssetImportResult {
  content_version: { id: string; version: number; digest: string | null };
  link: ProviderAssetLink;
}
