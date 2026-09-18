// Endpoint key 使用 string 别名；后端 ENDPOINT_REGISTRY 是真相源，前端通过
// GET /api/v1/custom-providers/endpoints 读取运行时 catalog，因此 endpoint 扩展
// 无需同步修改前端联合类型。
export type EndpointKey = string;

export type MediaType = "text" | "image" | "video" | "audio";

export type ImageCap = "text_to_image" | "image_to_image";

/** 模型发现协议：决定「模型发现」与「连通性检查」按哪套接口进行，不决定模型的调用协议。
 *  comfyui 取值下模型发现不适用，只做连通性检查。 */
export type DiscoveryFormat = "openai" | "google" | "comfyui";

export interface EndpointDescriptor {
  key: string;
  media_type: MediaType;
  family: string;
  /** 实现形态：python = backend 代码，declarative = 声明式定义，comfyui = 一份 ComfyUI workflow。 */
  kind: "python" | "declarative" | "comfyui";
  /** 归属：builtin = 随版发布、只读，custom = 用户自建，可编辑删除。 */
  source: "builtin" | "custom";
  display_name_key: string;
  /** 声明式端点的显示名（定义里的 meta.name，专有名词不翻译）；Python 内置为 null，取 display_name_key 的文案。 */
  display_name: string | null;
  request_method: string;
  request_path_template: string;
  /** image 类 endpoint 填能力数组，其他媒体类型为 null。 */
  image_capabilities: ImageCap[] | null;
  /** 执行层是否真的下传尾帧约束；仅 video 类有意义，其余恒为 false。 */
  end_image_capable: boolean;
  /** 尺寸由端点固定（ComfyUI 端点上宽高不是两侧都绑了节点）：比例与分辨率选择对它无效。 */
  size_fixed: boolean;
  /** 时长由端点固定（ComfyUI 端点上 frames 未绑定节点）：决定只读态的文案说哪一句。 */
  duration_fixed: boolean;
  /** 档位根本给不出来（frames 未绑定，或绑了却没有帧率来源）：档位不可编辑，成片长度以 workflow 为准。 */
  duration_tier_empty: boolean;
  /** 不选分辨率档位时这份 workflow 实际会出的那一档；非 ComfyUI 端点或读不出字面尺寸时为 null。 */
  native_resolution: string | null;
}

export interface CustomProviderInfo {
  id: number;
  display_name: string;
  discovery_format: DiscoveryFormat;
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
  /** 正在引用该模型的全局 system_settings 键名（如 default_video_backend_i2v）；未被引用为 null。 */
  global_bucket_refs: string[] | null;
}

/** 后端接受参考音频的运输形态；none 表示该模型没有音色输入通道。 */
export type ReferenceAudioMode = "none" | "direct";

/** 后端 VideoCapabilities 中界面用得到的子集（后端另有 reference_audio_per_image、
 *  max_reference_audio_total_seconds、max_prompt_chars 等纯执行期维度，不在此声明）。
 *  参考图路径以 max_reference_images > 0 表达，
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

/** 模型发现的返回。``not_applicable`` 为真时 ``models`` 恒空，``reason`` 是可直接展示的说明：
 *  该协议本就没有这一步，不是一次「什么都没发现」的失败。 */
export interface DiscoverModelsResponse {
  models: DiscoveredModel[];
  not_applicable: boolean;
  reason: string | null;
}

export interface DiscoveredModel {
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
}

export interface CustomProviderCreateRequest {
  display_name: string;
  discovery_format: DiscoveryFormat;
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
