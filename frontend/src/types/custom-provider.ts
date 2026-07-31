// Endpoint key 改用 string 别名 —— 真相源在后端 ENDPOINT_REGISTRY，
// 前端通过 GET /api/v1/custom-providers/endpoints 拉运行时 catalog。
// 放弃编译期窄类型换取「新增 endpoint 不再需要改前端类型」。
export type EndpointKey = string;

export type MediaType = "text" | "image" | "video" | "audio";

export type ImageCap = "text_to_image" | "image_to_image";

export interface EndpointDescriptor {
  key: string;
  media_type: MediaType;
  family: string;
  display_name_key: string;
  request_method: string;
  request_path_template: string;
  /** image 类 endpoint 填能力数组，其他媒体类型为 null。 */
  image_capabilities: ImageCap[] | null;
  /** 执行层是否真的下传尾帧约束；仅 video 类有意义，其余恒为 false。 */
  end_image_capable: boolean;
}

export interface CustomProviderInfo {
  id: number;
  display_name: string;
  discovery_format: "openai" | "google";
  base_url: string;
  api_key_masked: string;
  models: CustomProviderModelInfo[];
  created_at: string;
  /** 各 lane 并发上限；null = 未设置（走全局默认）。 */
  image_max_workers: number | null;
  video_max_workers: number | null;
  audio_max_workers: number | null;
}

export interface CustomProviderModelInfo {
  id: number;
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
  price_unit: string | null;
  price_input: number | null;
  price_output: number | null;
  currency: string | null;
  supported_durations: number[] | null;
  resolution: string | null;
  /** 系统判定的视频能力（全字段）；非视频模型为 null。 */
  system_capabilities: VideoCapabilityFlags | null;
  /** 用户覆盖（稀疏），与 system_capabilities 合并即为生效值；无覆盖为 null。 */
  capability_overrides: CapabilityOverrides | null;
}

/** 后端接受参考音频的运输形态；none 表示该模型没有音色输入通道。 */
export type ReferenceAudioMode = "none" | "direct";

/** 与后端 VideoCapabilities 字段对齐。参考图路径以 max_reference_images > 0 表达，
 *  不另设布尔位——两份声明会漂移出「称支持但上限为 0」这类自相矛盾的状态。 */
export interface VideoCapabilityFlags {
  first_frame: boolean;
  last_frame: boolean;
  max_reference_images: number;
  reference_audio_mode: ReferenceAudioMode;
  max_reference_audio_count: number;
}

/** 稀疏覆盖字典：键缺席 = 跟随系统判定。
 *  当前后端开放 last_frame / reference_audio_mode / max_reference_audio_count。 */
export type CapabilityOverrides = Partial<VideoCapabilityFlags>;

export interface DiscoveredModel {
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
}

export interface CustomProviderCreateRequest {
  display_name: string;
  discovery_format: "openai" | "google";
  base_url: string;
  api_key: string;
  models: CustomProviderModelInput[];
  /** 各 lane 并发上限；省略或 null = 未设置（走全局默认）。 */
  image_max_workers?: number | null;
  video_max_workers?: number | null;
  audio_max_workers?: number | null;
}

export interface CustomProviderFullUpdateRequest {
  display_name: string;
  base_url: string;
  api_key?: string;
  models: CustomProviderModelInput[];
  /** PUT 为并发上限权威来源：必填，null 即清除（走全局默认）。省略字段会被服务端当作
   *  清空，故类型上设为必填，防止调用方静默漏传意外清掉已有配置。 */
  image_max_workers: number | null;
  video_max_workers: number | null;
  audio_max_workers: number | null;
}

export interface CustomProviderModelInput {
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
  price_unit?: string;
  price_input?: number;
  price_output?: number;
  currency?: string;
  supported_durations?: number[] | null;
  resolution?: string | null;
  /** 保存模型列表是整体替换语义：省略该字段会清空已有覆盖，编辑既有模型时必须原样回传。 */
  capability_overrides?: CapabilityOverrides | null;
}

export interface CustomProviderCredentials {
  base_url: string;
  api_key: string;
}

export interface AnthropicDiscoverRequest {
  base_url?: string;
  api_key?: string;
}

export interface AnthropicDiscoverResponse {
  models: Array<{
    model_id: string;
    display_name: string;
    endpoint: string;
    is_default: boolean;
    is_enabled: boolean;
  }>;
}
