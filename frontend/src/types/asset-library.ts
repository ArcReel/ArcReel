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

/** 新增图片内容版本的保存位置（全局设置，缺省本地）。 */
export type NewImageTarget = "local" | "object_storage";

/** 「对象存储」块读取的全部状态：连接、新增图片保存位置，以及尚未清理的中转副本与待删除图片对象数量。 */
export interface ObjectStorageSettings {
  connection: ObjectStorageConnection | null;
  new_image_target: NewImageTarget;
  retained_relay_objects: number;
  /** 没有任何引用、还没有删除的图片对象（多为保存失败留下的）；更换桶或 Endpoint、删除连接后不再自动删除。 */
  unreleased_image_objects: number;
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

/** 供应商素材库里的一条素材。可用的图片素材可以选择导入。 */
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

/**
 * 供应商素材关联连同它的入库任务的状态：
 * pending 等待中转上传与提交，submitting 已提交，processing 供应商处理中，active 可用，
 * failed 失败，uncertain 提交结果不确定（后台按名称查回，不重新创建）。
 */
export type ProviderAssetLinkState = "pending" | "submitting" | "processing" | "active" | "failed" | "uncertain";

/** 图片某个内容版本在一个素材库范围里的关联或入库任务。 */
export interface ProviderAssetLink {
  id: number;
  content_version_id: string;
  version: number;
  account_id: number;
  /** 提交或导入时的调用范围，与账号的 scope_key 相等才属于该账号；账号换了 Access Key 后不再相等。 */
  scope_key: string;
  site: AssetLibrarySite;
  region: string;
  provider_project_name: string;
  state: ProviderAssetLinkState;
  selected: boolean;
  /** 只有可用的关联才会用于生成。 */
  usable: boolean;
  provider_asset_id: string | null;
  /** ArcReel 侧的失败（snake_case，如 relay_upload_failed）或供应商原样返回的错误码。 */
  error_code: string | null;
  error_message: string | null;
  check_error: string | null;
  /** 后台仍会自动查询；为 false 且状态未定时由用户手动刷新。 */
  polling: boolean;
  created_at: string | null;
  submitted_at: string | null;
  settled_at: string | null;
}

export interface ProviderAssetLinkAccount {
  id: number;
  /** 账号按当前云账号身份、站点、区域与项目覆盖的范围。 */
  scope_key: string;
  name: string;
  site: AssetLibrarySite;
  region: string;
  project_name: string;
}

/** 图片当前选中版本的素材库关联面板。 */
export interface ProviderAssetLinkPanel {
  content_version: { id: string | null; version: number; digest: string | null } | null;
  object_storage_configured: boolean;
  accounts: ProviderAssetLinkAccount[];
  links: ProviderAssetLink[];
}

/** 从素材库选择已有图片的结果：保存得到的新版本与它的可用关联。 */
export interface ProviderAssetImportResult {
  content_version: { id: string; version: number; digest: string | null };
  link: ProviderAssetLink;
}
