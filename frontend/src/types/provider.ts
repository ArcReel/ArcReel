import type { CapabilityOverrides, EndpointRequest, MatchedCatalogEntry, PathOverrides } from "./custom-provider";

/**
 * 声音一致性三级标识，与 `lib/config/resolver.py::VoiceConsistency` 一一对应。
 * 档位值全部由服务端派生，前端只做渲染映射，不复制派生公式。
 */
export type VoiceConsistencyTier = "native" | "soft" | "none";

/**
 * 成片音轨形态，与 `packages/arcreel-market-core/src/arcreel_market_core/video_backend_contract.py::VideoAudioMode` 一一对应：
 * `controllable` = 请求带音轨开关，用户的开/关意图能抵达供应商；`always_on` = 恒有声、无开关；
 * `always_off` = 该路径不产音轨、也无开关。三态由服务端从 backend 声明派生，前端不合成。
 */
export type VideoAudioControl = "controllable" | "always_on" | "always_off";

/**
 * 视频执行路径（任务类型桶），与 `lib/config/resolver.py::VideoGenerationType` 一一对应：
 * `i2v` 覆盖文生与图生首帧，`r2v` 是参考生视频。逐路径的能力位按它取值。
 */
export type VideoRoute = "i2v" | "r2v";

export interface ModelInfoResponse {
  display_name: string;
  media_type: string;
  capabilities: string[];
  default: boolean;
  // 型号声明的时长全集。分辨率 / 参考图联动约束不在目录里：收窄结果由 video-capabilities
  // 端点按上下文算好回传（见 types/project.ts::DurationConstraints），前端不持有规则。
  supported_durations: number[];
  resolutions: string[];
  // 成片音轨形态（可控 / 恒有声 / 恒无声），按执行路径各给一份：同一 model 在图生与参考生两条
  // 子路径上的请求形态可以不同（可灵 v3-omni 的多图主体子路径不带音轨开关，成片必然无声）。
  // 服务端从 backend 的 VideoCapabilities 派生，前端只取值、不合成三态；非视频 model 恒
  // always_off。见 utils/provider-models.ts::lookupVideoAudioControl。
  audio_track: VideoAudioControl;
  reference_route_audio_track: VideoAudioControl;
  // 无项目上下文时的声音一致性档位，服务端派生（generation_mode 未知，native 恒降格）。
  // 有项目上下文的页面改用 /projects/{name}/video-capabilities 的同名字段，不读这里。
  voice_consistency: VoiceConsistencyTier;
}

export interface ProviderInfo {
  id: string;
  display_name: string;
  description: string;
  status: "ready" | "unconfigured" | "error";
  media_types: string[];
  capabilities: string[];
  /** 凭证（界面称「密钥」）条数；没有凭证时为 0。 */
  credential_count: number;
  models: Record<string, ModelInfoResponse>;
}

export interface ProviderField {
  key: string;
  label: string;
  type: "secret" | "text" | "url" | "number" | "file";
  required: boolean;
  is_set: boolean;
  value?: string;
  value_masked?: string;
  placeholder?: string;
}

// 凭证表单需渲染的 secret 输入字段（后端按 required ∩ secret ∩ 凭证键派生，单一真相源）。
// 单 secret provider → [api_key]；可灵 → [access_key, secret_key]。
export interface CredentialSecretField {
  key: string;
  label: string;
}

export interface ProviderConfigDetail {
  id: string;
  display_name: string;
  description: string;
  status: "ready" | "unconfigured" | "error";
  media_types?: string[];
  fields: ProviderField[];
  // 凭证是否支持自定义 base_url（后端按 optional_keys 派生，单一真相源）
  supports_base_url: boolean;
  // 凭证表单应渲染的 secret 字段（有序）
  secret_fields: CredentialSecretField[];
  // 凭证「二选一」分组：满足任一组（组内字段全填）即视为凭证完整；单组场景（绝大多数
  // provider）等价于「全部 secret_fields 必填」的旧语义。可灵为 [["api_key"], ["access_key", "secret_key"]]。
  secret_field_groups: string[][];
}

export interface ConnectivityCheckResult {
  success: boolean;
  available_models: string[];
  message: string;
}

export interface ProviderCredential {
  id: number;
  provider: string;
  name: string;
  api_key_masked: string | null;
  credentials_filename: string | null;
  base_url: string | null;
  // 逐字段独立脱敏的双 secret（可灵）；其余 provider 为 null/缺省
  access_key_masked?: string | null;
  secret_key_masked?: string | null;
  is_active: boolean;
  created_at: string;
}

export type CallType = "image" | "video" | "text" | "audio";


/** 价格摘要里一个计费项的单价范围；`per` 是单价对应的计数量（每百万 token 为 1000000，每张、每秒为 1）。 */
export interface PriceSummaryItem {
  meter: string;
  min_price: number;
  max_price: number;
  per: number;
}

export interface PriceSummary {
  currency: string;
  items: PriceSummaryItem[];
}

/**
 * 供应商模型的状态标记：`discontinued` 已下架；`unpriced` 调用费用会记为未定价；
 * `modified` 用户改过对应的模型信息，不再随官方更新。
 */
export type ProviderModelFlag = "discontinued" | "unpriced" | "modified";

/** 供应商详情模型列表的一行（GET /providers/{id}/models），内置与自定义供应商共用。 */
export interface ProviderModelItem {
  /** 设置与项目里引用这个模型的模型键。 */
  model_id: string;
  api_model_name: string;
  /** 没有显示名时界面显示模型 ID。 */
  display_name: string | null;
  media_type: "text" | "image" | "video" | "audio";
  enabled: boolean;
  is_default: boolean;
  /** 内置供应商官方收录的模型，只能启用、停用。 */
  official: boolean;
  flags: ProviderModelFlag[];
  price: PriceSummary | null;
  /** 自定义供应商模型的调用端点键；内置供应商为 null。 */
  endpoint: string | null;
}

/** 供应商模型列表（GET /providers/{id}/models）；`discoverable` 为真时可以「获取模型」。 */
export interface ProviderModelsResponse {
  models: ProviderModelItem[];
  discoverable: boolean;
}

/**
 * 「获取模型」获取到的一个模型（POST /providers/{id}/models/discover）。
 * - `recognized`：自定义供应商为名字对应上了条目；内置供应商为对上了清单里官方收录的模型，`model_key` 是它的模型键。
 * - `media_type`：对应上的取条目的，对应不上的按名字推断。
 * - `endpoint`、`endpoint_by_media_type`：自定义供应商的默认调用端点，以及改为对应其他媒体类型的条目时各媒体类型下的
 *   默认端点；内置供应商为 null。
 */
export interface FetchedProviderModel {
  model_id: string;
  display_name: string;
  media_type: ProviderModelItem["media_type"];
  recognized: boolean;
  model_key: string | null;
  matched_entry: MatchedCatalogEntry | null;
  endpoint: string | null;
  endpoint_by_media_type: Partial<Record<ProviderModelItem["media_type"], string>> | null;
  is_default: boolean;
}

/** 获取模型的结果；`not_applicable` 为真时这个供应商没有模型列表接口，`reason` 是可直接展示的说明。 */
export interface FetchProviderModelsResponse {
  models: FetchedProviderModel[];
  not_applicable: boolean;
  reason: string | null;
}

/** 按 ID 添加模型（POST /providers/{id}/models）。`endpoint` 只用于自定义供应商，不给时按名字推断。 */
export interface AddProviderModelRequest {
  model_id: string;
  media_type?: ProviderModelItem["media_type"];
  entry_binding?: string | null;
  endpoint?: string | null;
}

export interface AddProviderModelResponse {
  model: ProviderModelItem;
  /** 对上了官方收录的模型，添加即启用它。 */
  official: boolean;
}

/** 能力覆盖的维度键。 */
export type CapabilityKey = keyof CapabilityOverrides;

/**
 * 「这个供应商支持的参数」（GET /providers/{id}/models/{model}）：
 * `base` 是没有能力覆盖时运行时采用的值（对应条目的值，对应不上时为运行时回落），`null` 表示不加约束；
 * `overrides` 是生效的能力覆盖；`editable` 是可以修改的维度，按界面顺序，官方收录的模型为空。
 */
export interface ProviderModelCapabilities {
  media_type: ProviderModelItem["media_type"];
  editable: CapabilityKey[];
  base: Partial<Record<CapabilityKey, unknown>>;
  overrides: CapabilityOverrides;
  effective: Partial<Record<CapabilityKey, unknown>>;
}

/** 价目表里一个选择维度的取值：上下文档的下界与时长秒数是数字，是否有声等是布尔，分辨率、服务档是文字。 */
export type PriceDimensionValue = string | number | boolean;

/**
 * 模型条目的价目表（目录数据的格式）：单币种，由若干计费项组成，每个计费项按选择维度分档，
 * 每档一行「条件 → 单价」。`defaults` 是请求缺少某个维度取值时使用的取值；`free_allowance` 是单请求免费额度。
 */
export interface CatalogPriceTable {
  currency: string;
  meters: {
    meter: string;
    dims: string[];
    rows: { when: Record<string, PriceDimensionValue>; price: number }[];
    defaults?: Record<string, PriceDimensionValue>;
    free_allowance?: number;
    cjk_double?: boolean;
  }[];
}

/** 模型信息由谁维护：跟随官方、用户改过、官方没有由用户填写。 */
export type CatalogEntrySource = "official" | "modified" | "user_created";

/** 条目身份：「模型厂商/官方模型名」，两栖模型再以媒体类型区分。 */
export interface CatalogEntryKey {
  maker: string;
  name: string;
  media_type: ProviderModelItem["media_type"];
}

/**
 * 供应商模型对应到的条目（合成后），Sheet 下段「模型信息」。
 * `official` 是用户改过时官方数据里的值；`shared_with` 是同样使用这个条目的其他供应商。
 */
export interface ProviderModelEntryInfo extends CatalogEntryKey {
  display_name: string;
  aliases: string[];
  discontinued: boolean;
  context_length: number | null;
  facts: Record<string, unknown>;
  /** 价目表；没有价目表时调用费用记为未定价。 */
  price: CatalogPriceTable | null;
  source: CatalogEntrySource;
  official: { display_name: string; facts: Record<string, unknown>; price: CatalogPriceTable | null } | null;
  shared_with: { provider_id: string; display_name: string }[];
}

/** 「模型信息」的修改：身份是 Sheet 读到的条目，`facts` 只给要改的模型事实。 */
export interface CatalogEntryUpdate extends CatalogEntryKey {
  display_name?: string;
  facts?: Record<string, unknown>;
  /** 整份价目表，整份替换；`null` 清除价目表。没有这个键表示不改。 */
  price?: CatalogPriceTable | null;
}

/** 供应商模型 Sheet 的内容。 */
export interface ProviderModelDetail {
  model: ProviderModelItem;
  /** 供应商模型倍率；官方收录的模型为 null（界面不显示）。 */
  multiplier: number | null;
  provider_multiplier: number;
  entry: ProviderModelEntryInfo | null;
  capabilities: ProviderModelCapabilities;
  /** 以下只有自定义供应商的模型有。 */
  endpoint: string | null;
  resolution: string | null;
  path_overrides: PathOverrides | null;
  requests: EndpointRequest[];
}

/** 保存供应商模型 Sheet 的供应商设置与模型信息；没有传的字段不改。 */
export interface ProviderModelSettingsUpdate {
  enabled?: boolean;
  is_default?: boolean;
  model_id?: string;
  multiplier?: number;
  capability_overrides?: CapabilityOverrides | null;
  endpoint?: string;
  path_overrides?: PathOverrides | null;
  resolution?: string | null;
  /** 修改对应的模型信息，存为用户副本，这个模型从此不再随官方更新。 */
  entry?: CatalogEntryUpdate;
}

/** 引用某个供应商模型的全局设置键与项目。 */
export interface ProviderModelReferences {
  settings: string[];
  projects: { name: string; title: string | null }[];
}
