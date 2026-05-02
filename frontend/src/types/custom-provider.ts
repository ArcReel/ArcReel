// Endpoint key 改用 string 别名 —— 真相源在后端 ENDPOINT_REGISTRY，
// 前端通过 GET /api/v1/custom-providers/endpoints 拉运行时 catalog。
// 放弃编译期窄类型换取「新增 endpoint 不再需要改前端类型」。
export type EndpointKey = string;

export type MediaType = "text" | "image" | "video";

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
}

export interface CustomProviderInfo {
  id: number;
  display_name: string;
  discovery_format: "openai" | "google";
  base_url: string;
  api_key_masked: string;
  models: CustomProviderModelInfo[];
  created_at: string;
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
  discovery_format: "openai" | "google";
  base_url: string;
  api_key: string;
  models: CustomProviderModelInput[];
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
}
