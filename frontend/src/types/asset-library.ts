/** Object storage connection (keys are masked on read). */
export interface ObjectStorageConnection {
  kind: "tos";
  region: string;
  endpoint: string;
  bucket: string;
  access_key_id_masked: string;
  secret_access_key_masked: string;
  updated_at: string;
}

/** Storage target for new image content versions (global setting, local by default). */
export type NewImageTarget = "local" | "object_storage";

/** Full state read by the Object Storage section: connection, storage target for new images, and counts of retained relay copies and image objects awaiting deletion. */
export interface ObjectStorageSettings {
  connection: ObjectStorageConnection | null;
  new_image_target: NewImageTarget;
  retained_relay_objects: number;
  /** Unreferenced image objects not yet deleted (mostly left after failed saves); no longer deleted automatically after changing the bucket or Endpoint, or deleting the connection. */
  unreleased_image_objects: number;
}

/** Save an object storage connection; for an existing connection, leaving either key field empty retains its saved value. */
export interface SaveObjectStorageConnectionPayload {
  region: string;
  endpoint: string;
  bucket: string;
  access_key_id?: string;
  secret_access_key?: string;
}

export type AssetLibrarySite = "volcengine" | "byteplus";

export interface AssetLibrarySiteInfo {
  id: AssetLibrarySite;
  regions: string[];
}

/** Asset library account: control-plane credentials with their site, region, and project scope (keys masked on read). */
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

/** One inference credential eligible for asset library binding, with its current binding. */
export interface AssetLibraryBindableCredential {
  credential_type: AssetLibraryCredentialType;
  credential_id: number;
  /** Built-in provider ID, or `custom-<id>` for a custom provider. */
  provider: string;
  name: string;
  api_key_masked: string | null;
  endpoint: string;
  binding: { account_id: number; status: AssetLibraryBindingStatus } | null;
}

export type ProviderAssetStatus = "active" | "processing" | "failed" | "unknown";

/** One asset in a provider asset library. Available image assets can be selected for import. */
export interface ProviderAsset {
  id: string;
  name: string;
  /** Asset type returned unchanged by the provider: Image / Video / Audio. */
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

/**
 * State of a provider asset link and its upload job:
 * pending awaits relay upload and submission, submitting has been submitted, processing is being processed by the provider, active is usable,
 * failed means failure, and uncertain means submission outcome is unknown (looked up by name in the background, without recreating).
 */
export type ProviderAssetLinkState = "pending" | "submitting" | "processing" | "active" | "failed" | "uncertain";

/** Link or upload job for one image content version within an asset library scope. */
export interface ProviderAssetLink {
  id: number;
  content_version_id: string;
  version: number;
  account_id: number;
  /** Invocation scope at submission or import; belongs to the account only when equal to its scope_key, which no longer matches after an Access Key change. */
  scope_key: string;
  site: AssetLibrarySite;
  region: string;
  provider_project_name: string;
  state: ProviderAssetLinkState;
  selected: boolean;
  /** Only usable links are used for generation. */
  usable: boolean;
  provider_asset_id: string | null;
  /** ArcReel-side failure (snake_case, such as relay_upload_failed) or the provider's unchanged error code. */
  error_code: string | null;
  error_message: string | null;
  check_error: string | null;
  /** The background still polls automatically; when false with an unsettled state, the user refreshes manually. */
  polling: boolean;
  created_at: string | null;
  submitted_at: string | null;
  settled_at: string | null;
}

export interface ProviderAssetLinkAccount {
  id: number;
  /** Scope covered by the account's current cloud identity, site, region, and project. */
  scope_key: string;
  name: string;
  site: AssetLibrarySite;
  region: string;
  project_name: string;
}

/** Asset library link panel for the image's currently selected version. */
export interface ProviderAssetLinkPanel {
  content_version: { id: string | null; version: number; digest: string | null } | null;
  object_storage_configured: boolean;
  accounts: ProviderAssetLinkAccount[];
  links: ProviderAssetLink[];
}

/** Result of selecting an existing image from the asset library: the newly saved version and its usable link. */
export interface ProviderAssetImportResult {
  content_version: { id: string; version: number; digest: string | null };
  link: ProviderAssetLink;
}
