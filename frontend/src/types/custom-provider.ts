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
  /** 档位为空的成因是读不到帧率来源（frames 绑了却既无 fps 绑定也没手填帧率）：同样只决定文案。 */
  duration_frame_rate_missing: boolean;
  /** 档位根本给不出来（frames 未绑定、没有帧率来源，或帧率有但换算不出整秒档位）：档位不可编辑，时长这一维不由 ArcReel 驱动。 */
  duration_tier_empty: boolean;
  /** 不选分辨率档位时这份 workflow 实际会出的那一档；非 ComfyUI 端点或读不出字面尺寸时为 null。 */
  native_resolution: string | null;
  /** 可修改请求地址的请求；空数组 = 该端点不开放修改。 */
  requests: EndpointRequest[];
}

/** 调用端点发出的一条可修改地址的请求（docs/adr/0101）。 */
export interface EndpointRequest {
  /** 请求名，模型的 path_overrides 按它记录。 */
  name: string;
  display_name_key: string;
  /** 归一化后的 base_url 之后的默认路径，可含 {{ model }}、{{ task_id }} 等占位符。 */
  default_path: string;
  placeholders: string[];
  /** 没有覆盖时 base_url 的归一化方式，界面据此还原最终地址；有覆盖时一律相对原样值。 */
  base_url_rule:
    | "as_is"
    | "strip_version"
    | "append_v1"
    | "ensure_api_v3"
    | "ensure_ent_v2"
    | "ensure_v1"
    | "dashscope_api_v1"
    | "strip_messages";
}

/** 请求名 → 整条替换的路径。 */
export type PathOverrides = Record<string, string>;

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
  resolution: string | null;
  /** 供应商模型倍率：与供应商倍率相乘，作用在这个模型的参考费用上。 */
  multiplier: number;
  /** 条目绑定「模型厂商/官方模型名」；null = 按模型名对应条目。整表保存时必须原样回传。 */
  entry_binding: string | null;
  /** 对应到的模型条目；null = 对应不上条目，这个模型的调用记为未定价。 */
  matched_entry: MatchedCatalogEntry | null;
  /** 系统判定的视频能力（全字段）；非视频模型为 null。 */
  system_capabilities: VideoCapabilityFlags | null;
  /** 用户覆盖（稀疏），与 system_capabilities 合并即为生效值；无覆盖为 null。 */
  capability_overrides: CapabilityOverrides | null;
  /** 生效的请求地址修改；无修改为 null。 */
  path_overrides: PathOverrides | null;
  /** 当前端点可修改地址的请求。 */
  requests: EndpointRequest[];
}

/** 供应商模型对应到的模型条目摘要。`priced` 为 false 时条目没有可用的价目表，调用同样记为未定价。 */
export interface MatchedCatalogEntry {
  maker: string;
  name: string;
  media_type: MediaType;
  display_name: string;
  priced: boolean;
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

/** 成片音轨形态：可控 / 恒有声 / 恒无声。 */
export type VideoAudioMode = "controllable" | "always_on" | "always_off";

/** 能力覆盖中视频的通道事实维度（输入模式、参考上限、提示词长度上限、音轨形态）。 */
export interface VideoChannelFactOverrides extends VideoCapabilityFlags {
  text_to_video: boolean;
  max_reference_audio_total_seconds: number | null;
  max_prompt_chars: number | null;
  audio_track: VideoAudioMode;
  reference_route_audio_track: VideoAudioMode | null;
}

/** 能力覆盖中的模型事实维度，按媒体类型开放：文本、图片、视频各自一组。 */
export interface ModelFactOverrides {
  image_input: boolean;
  structured_output: boolean;
  max_output_tokens: number;
  text_to_image: boolean;
  image_to_image: boolean;
  durations: number[];
  resolutions: string[];
  duration_constraints: {
    by_resolution?: Record<string, number[]>;
    with_reference_images?: number[];
  };
}

/** 稀疏覆盖字典：键缺席 = 该维度跟随条目与调用通道声明，出现即整维替换。 */
export type CapabilityOverrides = Partial<VideoChannelFactOverrides & ModelFactOverrides>;

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
  /** 发现接口给出的最大输出长度（目前只有 Google 协议的文本模型）；拿不到为 null。供应商表单不读它。 */
  max_output_tokens?: number | null;
  /** 按模型名对应到的模型条目；null = 对应不上，调用记为未定价。 */
  matched_entry?: MatchedCatalogEntry | null;
  /** 调用端点的媒体类型。 */
  media_type?: MediaType;
  /** 对应上了条目（已识别）。 */
  recognized?: boolean;
  /** 改为对应其他媒体类型的条目时，各媒体类型下的默认调用端点。 */
  endpoint_by_media_type?: Record<MediaType, EndpointKey> | null;
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
  /** 不传时不改模型：详情页的模型经供应商模型接口逐个保存，整表回传会覆盖其间单行的启停与设置。 */
  models?: CustomProviderModelInput[];
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
  resolution?: string | null;
  /** 保存模型列表是整体替换语义：省略该字段会清空已有覆盖，编辑既有模型时必须原样回传。 */
  capability_overrides?: CapabilityOverrides | null;
  /** 请求地址修改，与 capability_overrides 同为整体替换语义，编辑既有模型时必须原样回传。 */
  path_overrides?: PathOverrides | null;
  /** 供应商模型倍率，非负有限数；省略为 1。 */
  multiplier?: number;
  /** 条目绑定；与覆盖同理，编辑既有模型时必须原样回传。 */
  entry_binding?: string | null;
}

/** 供应商倍率：`multiplier` 是生效值，`default` 是没有设置时的值。 */
export interface ProviderMultiplierInfo {
  multiplier: number;
  default: number;
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
