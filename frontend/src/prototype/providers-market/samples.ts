// PROTOTYPE — #2972 的「原型样例」数据。本机真实数据里自定义供应商、密钥与市场条目都是空的，
// 空列表暴露不出密度、截断与对齐问题，所以按真实形状补一批样例；界面上统一标「样例」。
// 端点目录、预置供应商、市场源与官方服务状态一律取真实接口。

import type { CustomProviderInfo, MarketEntry, MarketSubmission, ProviderCredential } from "@/types";

export interface ProtoModel {
  key: string;
  model_id: string;
  endpoint: string;
  is_enabled: boolean;
  is_default: boolean;
  currency: "USD" | "CNY";
  price_input: string;
  price_output: string;
  resolution: string;
  durations: string;
  /** auto = 跟随系统判定 */
  last_frame: "auto" | "on" | "off";
  max_output_tokens: string;
  global_refs: string[];
}

export interface ProtoCustomProvider {
  id: number;
  display_name: string;
  discovery_format: string;
  base_url: string;
  api_key_masked: string;
  models: ProtoModel[];
  workers: { image: string; video: string; audio: string };
  sample: boolean;
}

let seq = 0;
function m(model_id: string, endpoint: string, extra: Partial<ProtoModel> = {}): ProtoModel {
  seq += 1;
  return {
    key: `m${seq}`,
    model_id,
    endpoint,
    is_enabled: true,
    is_default: false,
    currency: "CNY",
    price_input: "",
    price_output: "",
    resolution: "",
    durations: "",
    last_frame: "auto",
    max_output_tokens: "",
    global_refs: [],
    ...extra,
  };
}

export const SAMPLE_CUSTOM_PROVIDERS: ProtoCustomProvider[] = [
  {
    id: -1,
    display_name: "OneAPI 中转（公司账号，2026 年 Q4 续费）",
    discovery_format: "openai",
    base_url: "https://oneapi.internal.example.com/v1",
    api_key_masked: "sk-••••••••3f9a",
    workers: { image: "4", video: "2", audio: "" },
    sample: true,
    models: [
      m("gpt-5.1", "openai-chat", { is_default: true, currency: "USD", price_input: "1.25", price_output: "10", max_output_tokens: "32768", global_refs: [] }),
      m("claude-sonnet-4-5-20250929", "openai-chat", { currency: "USD", price_input: "3", price_output: "15", max_output_tokens: "64000" }),
      m("deepseek-v3.2-exp", "openai-chat", { price_input: "2", price_output: "3", max_output_tokens: "8192" }),
      m("gemini-3-pro-preview", "gemini-generate", { currency: "USD", price_input: "2", price_output: "12", max_output_tokens: "65536", is_enabled: false }),
      m("gpt-image-1.5", "openai-images", { is_default: true, currency: "USD", price_input: "0.04", resolution: "1536x1024", global_refs: ["default_image_backend"] }),
      m("qwen-image-edit-plus-2025-10-30", "openai-images-edits", { price_input: "0.2", resolution: "1328x1328" }),
      m("doubao-seedream-4-0-250828", "openai-images-generations", { price_input: "0.2", resolution: "2048x2048" }),
      m("doubao-seedance-1-0-pro-250528", "ark-seedance", { is_default: true, price_input: "3.67", resolution: "1080p", durations: "5, 10", last_frame: "on", global_refs: ["default_video_backend", "default_video_backend_i2v"] }),
      m("kling-v2-1-master-image2video", "kling-video", { price_input: "7", resolution: "1080p", durations: "5, 10" }),
      m("MiniMax-Hailuo-02", "minimax-hailuo-v1", { price_input: "2", resolution: "768p", durations: "6, 10", last_frame: "off" }),
      m("wan2.5-i2v-preview", "dashscope-async-video", { price_input: "1", resolution: "720p", durations: "5, 10" }),
      m("sora-2-pro", "openai-video", { currency: "USD", price_input: "0.5", resolution: "1280x720", durations: "4, 8, 12", is_enabled: false }),
      m("tts-1-hd", "openai-tts", { currency: "USD", price_input: "30" }),
      m("", "newapi-video", { is_enabled: true }),
    ],
  },
  {
    id: -2,
    display_name: "AutoDL ComfyUI",
    discovery_format: "comfyui",
    base_url: "https://u123456-8188.westc.gpuhub.com:8443",
    api_key_masked: "",
    workers: { image: "", video: "1", audio: "" },
    sample: true,
    models: [m("minimax-h3-lightx2v-15s", "ce-1", { resolution: "", durations: "15" })],
  },
  {
    id: -3,
    display_name: "本地 Ollama",
    discovery_format: "openai",
    base_url: "http://127.0.0.1:11434/v1",
    api_key_masked: "",
    workers: { image: "", video: "", audio: "" },
    sample: true,
    models: [
      m("qwen3:32b", "openai-chat", { max_output_tokens: "8192" }),
      m("llama3.3:70b-instruct-q4_K_M", "openai-chat", { max_output_tokens: "4096", is_enabled: false }),
    ],
  },
];

export function fromRealCustomProvider(p: CustomProviderInfo): ProtoCustomProvider {
  return {
    id: p.id,
    display_name: p.display_name,
    discovery_format: p.discovery_format,
    base_url: p.base_url,
    api_key_masked: p.api_key_masked,
    workers: {
      image: p.image_max_workers?.toString() ?? "",
      video: p.video_max_workers?.toString() ?? "",
      audio: p.audio_max_workers?.toString() ?? "",
    },
    sample: false,
    models: p.models.map((x) =>
      m(x.model_id, x.endpoint, {
        is_enabled: x.is_enabled,
        is_default: x.is_default,
        currency: x.currency === "USD" ? "USD" : "CNY",
        price_input: x.price_input?.toString() ?? "",
        price_output: x.price_output?.toString() ?? "",
        resolution: x.resolution ?? "",
        durations: x.supported_durations?.join(", ") ?? "",
        last_frame: x.capability_overrides?.last_frame === undefined ? "auto" : x.capability_overrides.last_frame ? "on" : "off",
        max_output_tokens: x.max_output_tokens?.toString() ?? "",
        global_refs: x.global_bucket_refs ?? [],
      }),
    ),
  };
}

export const SAMPLE_CREDENTIALS: Record<string, ProviderCredential[]> = {
  ark: [
    { id: -11, provider: "ark", name: "主账号（生产）", api_key_masked: "••••••••a1c4", credentials_filename: null, base_url: null, is_active: true, created_at: "2026-08-01T00:00:00Z" },
    { id: -12, provider: "ark", name: "测试账号 - 仅限内部演示与压测使用", api_key_masked: "••••••••77e0", credentials_filename: null, base_url: "https://ark.cn-beijing.volces.com/api/v3", is_active: false, created_at: "2026-09-12T00:00:00Z" },
  ],
  "gemini-aistudio": [
    { id: -13, provider: "gemini-aistudio", name: "个人", api_key_masked: "AIza••••••••Qk2", credentials_filename: null, base_url: null, is_active: true, created_at: "2026-07-01T00:00:00Z" },
  ],
};

function entry(slug: string, name: string, media: "image" | "video", author: string, description: string, extra: Partial<MarketEntry> = {}): MarketEntry {
  return {
    source_id: 1,
    source_display_name: "ArcReel Market",
    type: "endpoint",
    slug,
    path: `endpoints/${slug}`,
    name,
    author,
    version: "1.0.0",
    media_type: media,
    description,
    homepage: null,
    icon: null,
    min_app_version: null,
    min_app_version_satisfied: true,
    installation: null,
    ...extra,
  };
}

export const SAMPLE_MARKET_ENTRIES: MarketEntry[] = [
  entry("minimax-h3", "MiniMax H3", "video", "ArcReel", "MiniMax H3 视频生成，支持首尾帧与多参考图。", {
    installation: { endpoint_id: 1, endpoint_key: "minimax-h3", endpoint_display_name: "MiniMax H3", installed_version: "1.0.0", state: "current", modified: false },
  }),
  entry("newapi-video", "NewAPI Video", "video", "ArcReel", "NewAPI 兼容的统一视频生成接口。", {
    version: "1.2.0",
    installation: { endpoint_id: 2, endpoint_key: "newapi-video", endpoint_display_name: "NewAPI Video", installed_version: "1.1.0", state: "update_available", modified: true },
  }),
  entry("v2-video-generations", "V2 Video Generations", "video", "community-relay", "常见中转站的 /v2/video/generations 形态。"),
  entry("autodl-minimax-h3-multi-image-15s", "AutoDL MiniMax H3 多图生视频 15 秒（LightX2V 加速版）", "video", "pollo", "ComfyUI 工作流：多图参考生成 15 秒视频，需自备 AutoDL 实例与 LightX2V 权重，首次运行会下载约 18 GB 模型文件。"),
  entry("seedream-4-relay", "Seedream 4.0（中转）", "image", "relay-hub", "火山 Seedream 4.0 经中转站调用，支持组图。"),
  entry("flux-kontext-pro", "FLUX.1 Kontext Pro", "image", "bfl-community", "图像编辑与风格一致性。"),
  entry("qwen-image-edit", "Qwen Image Edit", "image", "dashscope-fans", "通义千问图像编辑，支持多图融合。", { min_app_version: "0.40.0", min_app_version_satisfied: false }),
  entry("hunyuan-video-i2v", "HunyuanVideo I2V", "video", "tencent-community", "混元图生视频。"),
  entry("vidu-q2-reference", "Vidu Q2 参考生视频", "video", "vidu-fans", "最多 7 张参考图。"),
  entry("pixverse-v5", "PixVerse V5", "video", "pixverse-community", "PixVerse 官方接口。"),
  entry("ideogram-v3", "Ideogram V3", "image", "ideogram-fans", "文字排版能力强的图像模型。"),
  entry("recraft-v3-svg", "Recraft V3 SVG", "image", "recraft-community", "矢量风格图像生成，适合图标与海报。"),
];

export const SAMPLE_SUBMISSIONS: MarketSubmission[] = [
  { endpoint_id: 3, endpoint_key: "ce-1", endpoint_display_name: "AutoDL MiniMax H3 多图生视频 15 秒", type: "endpoint", slug: "autodl-minimax-h3-multi-image-15s", status: "merged", pr_url: "https://github.com/ArcReel/arcreel-market/pull/41", stale: false },
  { endpoint_id: 4, endpoint_key: "v2-video-generations", endpoint_display_name: "V2 Video Generations", type: "endpoint", slug: "v2-video-generations", status: "open", pr_url: "https://github.com/ArcReel/arcreel-market/pull/57", stale: true },
];

/** 端点被哪些样例模型引用：给「调用端点」详情的反向链接用。 */
export function referencesOf(endpointKey: string, providers: ProtoCustomProvider[]) {
  return providers.flatMap((p) =>
    p.models.filter((x) => x.endpoint === endpointKey && x.model_id).map((x) => ({ provider: p, model: x })),
  );
}
