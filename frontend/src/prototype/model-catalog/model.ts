// PROTOTYPE — #3091 统一模型管理界面。三个变体共用的类型、标签与纯函数，不写后端。
// 领域规则以 ADR 0098–0103 为准：条目三层合成、价目表「计量项 × 选择维度」、两级倍率、路径覆盖、能力按维度分源。

export type MediaType = "text" | "image" | "video" | "audio";
export const MEDIA_TYPES: MediaType[] = ["text", "image", "video", "audio"];
export const MEDIA_LABEL: Record<MediaType, string> = { text: "文本", image: "图片", video: "视频", audio: "音频" };

export type MeterKey =
  | "input_tokens"
  | "cache_read_tokens"
  | "cache_write_5m_tokens"
  | "cache_write_1h_tokens"
  | "output_tokens"
  | "image_input_tokens"
  | "image_cache_read_tokens"
  | "image_output_tokens"
  | "output_images"
  | "input_images"
  | "output_seconds"
  | "video_tokens"
  | "characters"
  | "requests";

export const METER_LABEL: Record<MeterKey, string> = {
  input_tokens: "未缓存输入",
  cache_read_tokens: "缓存读",
  cache_write_5m_tokens: "缓存写（5 分钟）",
  cache_write_1h_tokens: "缓存写（1 小时）",
  output_tokens: "输出",
  image_input_tokens: "图片输入",
  image_cache_read_tokens: "图片缓存读",
  image_output_tokens: "图片输出",
  output_images: "输出张数",
  input_images: "参考图张数",
  output_seconds: "输出秒数",
  video_tokens: "视频 token",
  characters: "字符数",
  requests: "次数",
};

export type MeterUnit = "per_1m" | "per_item" | "per_second" | "per_10k";
export const UNIT_LABEL: Record<MeterUnit, string> = {
  per_1m: "每百万 token",
  per_item: "每张 / 每次",
  per_second: "每秒",
  per_10k: "每万字符",
};
export const UNIT_SHORT: Record<MeterUnit, string> = { per_1m: "/M", per_item: "/次", per_second: "/秒", per_10k: "/万字" };

/** 选择维度由代码定义，目录数据不能自创（ADR 0099）。 */
export type DimKey = "context_tier" | "resolution" | "audio" | "quality" | "service_tier" | "duration";
export const DIM_LABEL: Record<DimKey, string> = {
  context_tier: "上下文档",
  resolution: "分辨率",
  audio: "有声",
  quality: "质量档",
  service_tier: "服务档",
  duration: "时长",
};
/** 每个选择维度的候选值，价格编辑器新增行时用。 */
export const DIM_VALUES: Record<DimKey, (string | number | boolean)[]> = {
  context_tier: ["≤200K", ">200K"],
  resolution: ["480p", "720p", "1080p", "4k", "1K", "2K", "4K", "512PX"],
  audio: [true, false],
  quality: ["std", "pro", "4k"],
  service_tier: ["default", "flex", "fast"],
  duration: [5, 6, 10],
};

export function dimValueLabel(dim: DimKey, v: string | number | boolean | null | undefined): string {
  if (v === null || v === undefined || v === "") return "任意";
  if (dim === "audio") return v ? "有声" : "无声";
  if (dim === "duration") return `${v} 秒`;
  if (dim === "service_tier") return ({ default: "标准", flex: "Flex", fast: "Fast" } as Record<string, string>)[String(v)] ?? String(v);
  return String(v);
}

export type DimValue = string | number | boolean | null;
export interface PriceRow {
  when: Partial<Record<DimKey, DimValue>>;
  price: number;
}
export interface Meter {
  meter: MeterKey;
  unit: MeterUnit;
  dims: DimKey[];
  rows: PriceRow[];
  /** 单请求免费额度，按计量项的计数单位计。 */
  freeAllowance?: number;
}
export type Currency = "USD" | "CNY";
export interface PriceTable {
  currency: Currency | null;
  meters: Meter[];
}

export type Capability = "text_generation" | "structured_output" | "vision" | "text_to_image" | "image_to_image" | "text_to_speech";
export const CAPABILITY_LABEL: Record<Capability, string> = {
  text_generation: "文本生成",
  structured_output: "结构化输出",
  vision: "图片输入",
  text_to_image: "文生图",
  image_to_image: "图生图",
  text_to_speech: "语音合成",
};

/** 一个条目某一层的完整内容。用户修改存整条副本（ADR 0098），不按字段存差异。 */
export interface EntryData {
  id: string;
  maker: string;
  name: string;
  displayName: string;
  mediaType: MediaType;
  capabilities: Capability[];
  durations: number[];
  resolutions: string[];
  durationConstraints: Record<string, number[]>;
  maxOutputTokens: number | null;
  aliases?: string[];
  price: PriceTable;
  /** 官方已下架。同步不删除条目，只打标记（ADR 0102）。 */
  deprecated?: boolean;
  /** 这一层最后变化时的数据集版本；用户副本记的是复制时所基于的官方版本。 */
  version: string;
}

export interface CatalogEntry {
  id: string;
  factory: EntryData | null;
  synced: EntryData | null;
  user: EntryData | null;
  syncOn: boolean;
}

export type EntryStatus =
  | "factory" // 跟随官方，用的是出厂快照
  | "synced" // 跟随官方，用的是比快照新的同步数据
  | "modified" // 已修改：同步关闭，官方从副本创建后没再变
  | "modified-outdated" // 已修改，且官方在此后又有更新
  | "user-created" // 目录里没有，由用户新建
  | "user-created-official"; // 用户新建，但官方后来也收录了同一身份：同步开关变为可打开

export const STATUS_LABEL: Record<EntryStatus, string> = {
  factory: "跟随官方",
  synced: "跟随官方",
  modified: "已修改",
  "modified-outdated": "已修改 · 官方有更新",
  "user-created": "用户新建",
  "user-created-official": "用户新建 · 官方已收录",
};

export function officialOf(e: CatalogEntry): EntryData | null {
  if (e.synced && e.factory) return e.synced.version > e.factory.version ? e.synced : e.factory;
  return e.synced ?? e.factory;
}

export function effectiveOf(e: CatalogEntry): EntryData {
  if (!e.syncOn && e.user) return e.user;
  return (officialOf(e) ?? e.user)!;
}

export function statusOf(e: CatalogEntry): EntryStatus {
  const official = officialOf(e);
  if (e.syncOn) return e.synced && (!e.factory || e.synced.version > e.factory.version) ? "synced" : "factory";
  if (!e.user) return "factory";
  const userCreated = e.user.version === "user";
  if (userCreated) return official ? "user-created-official" : "user-created";
  if (official && official.version > e.user.version) return "modified-outdated";
  return "modified";
}

export function isUserOwned(e: CatalogEntry): boolean {
  return !e.syncOn;
}

export const MAKER_LABEL: Record<string, string> = {
  google: "Google",
  openai: "OpenAI",
  anthropic: "Anthropic",
  bytedance: "字节跳动",
  alibaba: "阿里巴巴",
  xai: "xAI",
  deepseek: "DeepSeek",
  moonshot: "月之暗面",
  zhipu: "智谱",
  minimax: "MiniMax",
  kuaishou: "快手",
  shengshu: "生数科技",
  agnes: "Agnes",
  bfl: "Black Forest Labs",
};
export const makerLabel = (m: string) => MAKER_LABEL[m] ?? m;

const CURRENCY_SYMBOL: Record<Currency, string> = { USD: "$", CNY: "¥" };
export function money(v: number, cur: Currency | null): string {
  const s = cur ? CURRENCY_SYMBOL[cur] : "";
  const digits = v !== 0 && Math.abs(v) < 0.1 ? 4 : 2;
  return `${s}${Number(v.toFixed(digits))}`;
}

/** 价目表的一行摘要，如「$2 /M 输入 · $12 /M 输出」或「¥0.6–1.2 /秒」。 */
export function priceSummary(p: PriceTable): string {
  if (!p.meters.length) return "未定价";
  const parts = p.meters
    .filter((m) => ["input_tokens", "output_tokens", "output_images", "output_seconds", "video_tokens", "characters", "requests"].includes(m.meter))
    .slice(0, 2)
    .map((m) => {
      const prices = m.rows.map((r) => r.price);
      const lo = Math.min(...prices);
      const hi = Math.max(...prices);
      const range = lo === hi ? money(lo, p.currency) : `${money(lo, p.currency)}–${money(hi, p.currency).replace(/^[$¥]/, "")}`;
      const label = m.meter === "input_tokens" ? " 输入" : m.meter === "output_tokens" ? " 输出" : "";
      return `${range}${UNIT_SHORT[m.unit]}${label}`;
    });
  return parts.join(" · ");
}

export function hasCacheMeters(p: PriceTable): boolean {
  return p.meters.some((m) => m.meter.startsWith("cache_") || m.meter === "image_cache_read_tokens");
}

/** 两层数据之间变了哪些字段，供差异提示用。 */
export interface FieldDiff {
  field: string;
  label: string;
  before: string;
  after: string;
}
export function diffEntries(a: EntryData, b: EntryData): FieldDiff[] {
  const out: FieldDiff[] = [];
  const push = (field: string, label: string, x: unknown, y: unknown, fmt: (v: never) => string = (v) => JSON.stringify(v)) => {
    if (JSON.stringify(x) !== JSON.stringify(y)) out.push({ field, label, before: fmt(x as never), after: fmt(y as never) });
  };
  push("displayName", "显示名", a.displayName, b.displayName, (v: string) => v);
  push("capabilities", "能力", a.capabilities, b.capabilities, (v: Capability[]) => v.map((c) => CAPABILITY_LABEL[c]).join("、") || "无");
  push("durations", "可选时长", a.durations, b.durations, (v: number[]) => (v.length ? `${v.join("、")} 秒` : "无"));
  push("resolutions", "分辨率", a.resolutions, b.resolutions, (v: string[]) => v.join("、") || "无");
  push("maxOutputTokens", "最大输出", a.maxOutputTokens, b.maxOutputTokens, (v: number | null) => (v ? `${v} token` : "未登记"));
  push("deprecated", "下架", !!a.deprecated, !!b.deprecated, (v: boolean) => (v ? "官方已下架" : "在售"));
  // 价目表按计量项逐项比较
  const meters = new Set([...a.price.meters.map((m) => m.meter), ...b.price.meters.map((m) => m.meter)]);
  for (const key of meters) {
    const ma = a.price.meters.find((m) => m.meter === key);
    const mb = b.price.meters.find((m) => m.meter === key);
    const fmt = (m: Meter | undefined, cur: Currency | null) =>
      !m
        ? "无"
        : m.rows
            .map((r) => {
              const when = m.dims.map((d) => dimValueLabel(d, r.when[d])).join(" / ");
              return `${when ? when + " " : ""}${money(r.price, cur)}`;
            })
            .join("；");
    const fa = fmt(ma, a.price.currency);
    const fb = fmt(mb, b.price.currency);
    if (fa !== fb || JSON.stringify(ma?.dims) !== JSON.stringify(mb?.dims))
      out.push({ field: `price.${key}`, label: `价格 · ${METER_LABEL[key]}`, before: fa, after: fb });
  }
  if (a.price.currency !== b.price.currency) out.push({ field: "price.currency", label: "币种", before: a.price.currency ?? "无", after: b.price.currency ?? "无" });
  return out;
}

// ---------- 供应商与供应商模型 ----------

export type EndpointKey =
  | "openai-chat"
  | "openai-responses"
  | "anthropic-messages"
  | "gemini-generate"
  | "openai-images"
  | "openai-chat-image"
  | "openai-video"
  | "newapi-video"
  | "openrouter-video"
  | "openai-tts";

export interface EndpointRequest {
  /** 请求名，路径覆盖按它记录（ADR 0100）。 */
  name: string;
  label: string;
  method: "GET" | "POST";
  defaultPath: string;
}
export interface EndpointDef {
  key: EndpointKey;
  label: string;
  mediaType: MediaType;
  kind: "python" | "declarative";
  /** 没有覆盖时，base_url 末尾不是 /vN 就自动补 /v1。 */
  autoV1: boolean;
  requests: EndpointRequest[];
}

export const ENDPOINTS: Record<EndpointKey, EndpointDef> = {
  "openai-chat": { key: "openai-chat", label: "OpenAI Chat", mediaType: "text", kind: "python", autoV1: true, requests: [{ name: "chat", label: "对话", method: "POST", defaultPath: "/chat/completions" }] },
  "openai-responses": { key: "openai-responses", label: "OpenAI Responses", mediaType: "text", kind: "python", autoV1: true, requests: [{ name: "responses", label: "生成", method: "POST", defaultPath: "/responses" }] },
  "anthropic-messages": { key: "anthropic-messages", label: "Anthropic Messages", mediaType: "text", kind: "python", autoV1: false, requests: [{ name: "messages", label: "消息", method: "POST", defaultPath: "/v1/messages" }] },
  "gemini-generate": { key: "gemini-generate", label: "Gemini generateContent", mediaType: "text", kind: "python", autoV1: false, requests: [{ name: "generate", label: "生成", method: "POST", defaultPath: "/v1beta/models/{model}:generateContent" }] },
  "openai-images": {
    key: "openai-images",
    label: "OpenAI Images",
    mediaType: "image",
    kind: "python",
    autoV1: true,
    requests: [
      { name: "generate", label: "文生图", method: "POST", defaultPath: "/images/generations" },
      { name: "edit", label: "图生图", method: "POST", defaultPath: "/images/edits" },
    ],
  },
  "openai-chat-image": { key: "openai-chat-image", label: "Chat Completions 出图", mediaType: "image", kind: "python", autoV1: true, requests: [{ name: "chat", label: "出图", method: "POST", defaultPath: "/chat/completions" }] },
  "openai-video": {
    key: "openai-video",
    label: "OpenAI Videos",
    mediaType: "video",
    kind: "python",
    autoV1: true,
    requests: [
      { name: "create", label: "提交", method: "POST", defaultPath: "/videos" },
      { name: "retrieve", label: "轮询", method: "GET", defaultPath: "/videos/{task_id}" },
      { name: "content", label: "取结果", method: "GET", defaultPath: "/videos/{task_id}/content" },
    ],
  },
  "newapi-video": {
    key: "newapi-video",
    label: "new-api 视频",
    mediaType: "video",
    kind: "declarative",
    autoV1: false,
    requests: [
      { name: "submit", label: "提交", method: "POST", defaultPath: "/v1/video/generations" },
      { name: "poll", label: "轮询", method: "GET", defaultPath: "/v1/video/generations/{task_id}" },
    ],
  },
  "openrouter-video": {
    key: "openrouter-video",
    label: "OpenRouter 视频",
    mediaType: "video",
    kind: "declarative",
    autoV1: false,
    requests: [
      { name: "submit", label: "提交", method: "POST", defaultPath: "/videos" },
      { name: "poll", label: "轮询", method: "GET", defaultPath: "/videos/{task_id}" },
    ],
  },
  "openai-tts": { key: "openai-tts", label: "OpenAI TTS", mediaType: "audio", kind: "python", autoV1: true, requests: [{ name: "speech", label: "合成", method: "POST", defaultPath: "/audio/speech" }] },
};

export interface UrlPreview {
  url: string;
  /** 未覆盖时自动补上的 /v1，界面上可以单独标出来。 */
  autoAdded: string | null;
  overridden: boolean;
}

/** 预览一条请求的完整 URL。覆盖相对于 base_url 原样值，有覆盖时不再自动补 /v1（ADR 0100）。 */
export function previewUrl(baseUrl: string, endpoint: EndpointDef, req: EndpointRequest, override: string | undefined, apiModelName: string): UrlPreview {
  const base = baseUrl.replace(/\/+$/, "");
  const fill = (path: string) => path.replaceAll("{model}", apiModelName);
  if (override && override.trim()) {
    const path = override.trim().startsWith("/") ? override.trim() : `/${override.trim()}`;
    return { url: base + fill(path), autoAdded: null, overridden: true };
  }
  const autoAdded = endpoint.autoV1 && !/\/v\d+[a-z]*$/.test(base) ? "/v1" : null;
  return { url: base + (autoAdded ?? "") + fill(req.defaultPath), autoAdded, overridden: false };
}

/** 能力覆盖：按维度整体替换（ADR 0103）。条目维度与通道维度都可覆盖。 */
export interface CapabilityOverride {
  durations?: number[];
  resolutions?: string[];
  inputModes?: ("t2v" | "first_frame" | "last_frame")[];
  maxReferenceImages?: number;
}
export const INPUT_MODE_LABEL = { t2v: "文生视频", first_frame: "首帧", last_frame: "尾帧" } as const;

export interface ProviderModel {
  key: string;
  /** 引用的条目；null 表示没有对应到任何条目，调用记为未定价。 */
  entryId: string | null;
  apiModelName: string;
  enabled: boolean;
  isDefault: boolean;
  /** official = 内置供应商下官方收录，全部跟随官方、只读；user = 用户负责。 */
  responsibility: "official" | "user";
  endpoint?: EndpointKey;
  /** 供应商模型倍率；官方收录的随官方数据下发。 */
  multiplier: number;
  capabilityOverride?: CapabilityOverride;
  pathOverrides?: Record<string, string>;
}

export interface Provider {
  id: string;
  kind: "builtin" | "custom";
  displayName: string;
  baseUrl?: string;
  /** 供应商倍率，用户偏好。 */
  multiplier: number;
  credentialCount: number;
  mediaTypes: MediaType[];
  /** 内置供应商「获取模型」返回的 id；null 表示没有列表接口。 */
  discoverable: boolean;
  discovered?: string[];
  models: ProviderModel[];
}

export function effectiveMultiplier(p: Provider, m: ProviderModel): number {
  return Number((p.multiplier * m.multiplier).toFixed(4));
}

/** 通道声明的视频能力（代码声明的一侧）。原型里按端点粗略给出。 */
export function channelVideoCaps(endpoint: EndpointKey | undefined): CapabilityOverride {
  if (endpoint === "newapi-video") return { inputModes: ["t2v", "first_frame"], maxReferenceImages: 0 };
  if (endpoint === "openrouter-video") return { inputModes: ["t2v", "first_frame", "last_frame"], maxReferenceImages: 3 };
  if (endpoint === "openai-video") return { inputModes: ["t2v", "first_frame"], maxReferenceImages: 1 };
  return { inputModes: ["t2v", "first_frame", "last_frame"], maxReferenceImages: 4 };
}

/** 新建自定义模型时的价格预设：由预设生成通用结构（ADR 0099）。 */
export type PricePreset = "token" | "image" | "second" | "character";
export const PRICE_PRESET_LABEL: Record<PricePreset, string> = { token: "按 token", image: "按张", second: "按秒", character: "按字符" };
export function priceFromPreset(preset: PricePreset, currency: Currency): PriceTable {
  switch (preset) {
    case "token":
      return {
        currency,
        meters: [
          { meter: "input_tokens", unit: "per_1m", dims: [], rows: [{ when: {}, price: 0 }] },
          { meter: "cache_read_tokens", unit: "per_1m", dims: [], rows: [{ when: {}, price: 0 }] },
          { meter: "output_tokens", unit: "per_1m", dims: [], rows: [{ when: {}, price: 0 }] },
        ],
      };
    case "image":
      return { currency, meters: [{ meter: "output_images", unit: "per_item", dims: [], rows: [{ when: {}, price: 0 }] }] };
    case "second":
      return { currency, meters: [{ meter: "output_seconds", unit: "per_second", dims: [], rows: [{ when: {}, price: 0 }] }] };
    case "character":
      return { currency, meters: [{ meter: "characters", unit: "per_10k", dims: [], rows: [{ when: {}, price: 0 }] }] };
  }
}
