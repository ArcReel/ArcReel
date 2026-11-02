/** 对象存储连接（读出时密钥已掩码）。 */
export interface ObjectStorageConnection {
  kind: "tos";
  region: string;
  endpoint: string;
  bucket: string;
  access_key_id_masked: string;
  secret_access_key_masked: string;
  updated_at: string;
}

/** 保存对象存储连接；已有连接时两个密钥字段留空表示保留已保存的值。 */
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

/** 素材库账号：控制面凭证连同它的站点、区域与项目范围（读出时密钥已掩码）。 */
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

/** 一条可绑定素材库的推理凭证及其当前绑定。 */
export interface AssetLibraryBindableCredential {
  credential_type: AssetLibraryCredentialType;
  credential_id: number;
  /** 内置供应商 id，或自定义供应商的 `custom-<id>`。 */
  provider: string;
  name: string;
  api_key_masked: string | null;
  endpoint: string;
  binding: { account_id: number; status: AssetLibraryBindingStatus } | null;
}

export type ProviderAssetStatus = "active" | "processing" | "failed" | "unknown";

/** 供应商素材库里的一条素材（只读浏览）。 */
export interface ProviderAsset {
  id: string;
  name: string;
  /** 供应商原样返回的类型：Image / Video / Audio。 */
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
