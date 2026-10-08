// PROTOTYPE — #3091 种子数据。
// 出厂快照取自真实的 PROVIDER_REGISTRY（export_snapshot.py 导出，数据集版本记为 2026.10.01）。
// 在它之上叠一次模拟的官方同步（2026.10.08）和几处用户修改，覆盖所有条目状态。
// 同步层里 Anthropic、DeepSeek 的价格是示意值；自定义供应商是虚构的中转站，路径形态取自 #3080 的调研。

import snapshot from "./snapshot.json";
import type { CatalogEntry, EntryData, Meter, PriceTable, Provider, ProviderModel } from "./model";

export const FACTORY_VERSION = "2026.10.01";
export const SYNCED_VERSION = "2026.10.08";
/** 「检查更新」时模拟拿到的下一个数据集版本。 */
export const NEXT_VERSION = "2026.10.09";

type SnapEntry = Omit<EntryData, "version">;
type SnapProvider = { id: string; displayName: string; mediaTypes: string[]; models: { entryId: string; apiModelName: string; isDefault: boolean; hidden: boolean }[] };
const snap = snapshot as unknown as { entries: SnapEntry[]; providers: SnapProvider[] };

const clone = <T,>(v: T): T => structuredClone(v);
const factory = new Map<string, EntryData>(snap.entries.map((e) => [e.id, { ...clone(e), version: FACTORY_VERSION }]));

function tokenPrice(currency: "USD" | "CNY", rows: Record<string, number>, tiers?: Record<string, Record<string, number>>): PriceTable {
  const meters: Meter[] = [];
  const keys = Object.keys(rows) as Meter["meter"][];
  for (const k of keys) {
    if (tiers?.[k]) {
      meters.push({ meter: k, unit: "per_1m", dims: ["context_tier"], rows: Object.entries(tiers[k]).map(([tier, price]) => ({ when: { context_tier: tier }, price })) });
    } else {
      meters.push({ meter: k, unit: "per_1m", dims: [], rows: [{ when: {}, price: rows[k] }] });
    }
  }
  return { currency, meters };
}

function textEntry(id: string, displayName: string, price: PriceTable, maxOutputTokens: number, extra: Partial<EntryData> = {}): EntryData {
  const [maker, name] = id.split("/");
  return {
    id,
    maker,
    name,
    displayName,
    mediaType: "text",
    capabilities: ["text_generation", "structured_output", "vision"],
    durations: [],
    resolutions: [],
    durationConstraints: {},
    maxOutputTokens,
    price,
    version: SYNCED_VERSION,
    ...extra,
  };
}

// ---------- 官方同步层（2026.10.08） ----------
const synced = new Map<string, EntryData>();
const syncedFrom = (id: string, patch: (e: EntryData) => void) => {
  const base = factory.get(id);
  if (!base) throw new Error(`seed: 快照里没有 ${id}`);
  const next = { ...clone(base), version: SYNCED_VERSION };
  patch(next);
  synced.set(id, next);
};

// 可灵视频从 std / pro 档改为按分辨率分档，有声加价也变了（#3082 第 7 节）
for (const id of ["kuaishou/kling-v3", "kuaishou/kling-v3-omni"]) {
  syncedFrom(id, (e) => {
    e.price.meters = [
      {
        meter: "output_seconds",
        unit: "per_second",
        dims: ["resolution", "audio"],
        rows: [
          { when: { resolution: "720p", audio: false }, price: 0.6 },
          { when: { resolution: "720p", audio: true }, price: 0.9 },
          { when: { resolution: "1080p", audio: false }, price: 0.8 },
          { when: { resolution: "1080p", audio: true }, price: 1.2 },
          { when: { resolution: "4k", audio: false }, price: 1.6 },
          { when: { resolution: "4k", audio: true }, price: 1.6 },
        ],
      },
    ];
  });
}
// Gemini 3.1 Pro：按上下文档分段，补上缓存读
syncedFrom("google/gemini-3.1-pro-preview", (e) => {
  e.price = tokenPrice(
    "USD",
    { input_tokens: 2, cache_read_tokens: 0.2, output_tokens: 12 },
    { input_tokens: { "≤200K": 2, ">200K": 4 }, cache_read_tokens: { "≤200K": 0.2, ">200K": 0.4 }, output_tokens: { "≤200K": 12, ">200K": 18 } },
  );
});
// Sora 2 已随 OpenAI Videos API 关停：同步不删除，只标记下架
syncedFrom("openai/sora-2", (e) => (e.deprecated = true));
syncedFrom("openai/sora-2-pro", (e) => (e.deprecated = true));
// 万相 2.7 文生视频调价（用户在此之前已改过它，用来演示「已修改 · 官方有更新」）
syncedFrom("alibaba/wan2.7-t2v", (e) => {
  e.price.meters[0].rows = e.price.meters[0].rows.map((r) => ({ ...r, price: Number((r.price * 0.8).toFixed(2)) }));
});
// DeepSeek V4 由第三方数据源补上价格（示意值）
syncedFrom("deepseek/deepseek-v4-flash", (e) => (e.price = tokenPrice("CNY", { input_tokens: 1, cache_read_tokens: 0.2, output_tokens: 2 })));
syncedFrom("deepseek/deepseek-v4-pro", (e) => (e.price = tokenPrice("CNY", { input_tokens: 4, cache_read_tokens: 1, output_tokens: 16 })));

// 新收录的条目（快照里没有）
const claudeSonnet = textEntry(
  "anthropic/claude-sonnet-4-5",
  "Claude Sonnet 4.5",
  tokenPrice(
    "USD",
    { input_tokens: 3, cache_read_tokens: 0.3, cache_write_5m_tokens: 3.75, cache_write_1h_tokens: 6, output_tokens: 15 },
    {
      input_tokens: { "≤200K": 3, ">200K": 6 },
      cache_read_tokens: { "≤200K": 0.3, ">200K": 0.6 },
      cache_write_5m_tokens: { "≤200K": 3.75, ">200K": 7.5 },
      cache_write_1h_tokens: { "≤200K": 6, ">200K": 12 },
      output_tokens: { "≤200K": 15, ">200K": 22.5 },
    },
  ),
  64000,
  { aliases: ["claude-sonnet-4-5-20250929", "anthropic/claude-sonnet-4.5"] },
);
synced.set(claudeSonnet.id, claudeSonnet);
const claudeHaikuOfficial = textEntry(
  "anthropic/claude-haiku-4-5",
  "Claude Haiku 4.5",
  tokenPrice("USD", { input_tokens: 1, cache_read_tokens: 0.1, cache_write_5m_tokens: 1.25, cache_write_1h_tokens: 2, output_tokens: 5 }),
  64000,
  { aliases: ["claude-haiku-4-5-20251001"] },
);
synced.set(claudeHaikuOfficial.id, claudeHaikuOfficial);

// ---------- 用户层 ----------
const user = new Map<string, EntryData>();
// 改过价格（基于快照），之后官方又调价
{
  const e = { ...clone(factory.get("alibaba/wan2.7-t2v")!), version: FACTORY_VERSION };
  e.price.meters[0].rows = e.price.meters[0].rows.map((r) => ({ ...r, price: Number((r.price * 0.9).toFixed(2)) }));
  user.set(e.id, e);
}
// 改过可选时长，官方没再变
{
  const e = { ...clone(factory.get("minimax/MiniMax-H3")!), version: FACTORY_VERSION };
  e.durations = [6, 10];
  user.set(e.id, e);
}
// 目录里没有，用户新建
const flux: EntryData = {
  id: "bfl/flux-kontext-pro",
  maker: "bfl",
  name: "flux-kontext-pro",
  displayName: "FLUX.1 Kontext Pro",
  mediaType: "image",
  capabilities: ["text_to_image", "image_to_image"],
  durations: [],
  resolutions: ["1K"],
  durationConstraints: {},
  maxOutputTokens: null,
  price: { currency: "USD", meters: [{ meter: "output_images", unit: "per_item", dims: [], rows: [{ when: {}, price: 0.04 }] }] },
  version: "user",
};
user.set(flux.id, flux);
// 用户先建了 Haiku，后来官方也收录了同一身份
user.set("anthropic/claude-haiku-4-5", {
  ...textEntry("anthropic/claude-haiku-4-5", "Claude Haiku 4.5", tokenPrice("USD", { input_tokens: 1, output_tokens: 5 }), 32000),
  version: "user",
});
const qwenVl: EntryData = {
  ...textEntry("alibaba/qwen-vl-max", "通义千问 VL Max", tokenPrice("CNY", { input_tokens: 3, output_tokens: 9 }), 8192),
  version: "user",
};
user.set(qwenVl.id, qwenVl);

export function seedEntries(): CatalogEntry[] {
  const ids = new Set([...factory.keys(), ...synced.keys(), ...user.keys()]);
  return [...ids].map((id) => {
    const u = user.get(id) ?? null;
    return { id, factory: clone(factory.get(id) ?? null), synced: clone(synced.get(id) ?? null), user: clone(u), syncOn: !u };
  });
}

/** 「检查更新」拿到的下一版数据：两处变化，其中一处落在用户已修改的条目上，应被跳过。 */
export function nextDataset(): Map<string, EntryData> {
  const out = new Map<string, EntryData>();
  const veo = clone(synced.get("google/veo-3.1-fast-generate-001") ?? factory.get("google/veo-3.1-fast-generate-001")!);
  veo.version = NEXT_VERSION;
  veo.price.meters[0].rows = veo.price.meters[0].rows.map((r) => ({ ...r, price: Number((r.price * 0.75).toFixed(3)) }));
  out.set(veo.id, veo);
  const h3 = clone(factory.get("minimax/MiniMax-H3")!);
  h3.version = NEXT_VERSION;
  h3.durations = [6, 10, 15];
  out.set(h3.id, h3);
  return out;
}

// ---------- 供应商 ----------
const MEDIA = (xs: string[]) => xs as Provider["mediaTypes"];

/** 官方收录的供应商模型倍率，随官方数据下发；方舟 Agent Plan 走订阅，供应商倍率默认 0。 */
const ENABLED_BY_DEFAULT = new Set(["gemini-aistudio", "ark", "dashscope", "kling"]);

function builtinProviders(): Provider[] {
  return snap.providers.map((p) => {
    const models: ProviderModel[] = p.models
      .filter((m) => !m.hidden)
      .map((m, i) => ({
        key: `${p.id}:${m.apiModelName}`,
        entryId: m.entryId,
        apiModelName: m.apiModelName,
        enabled: ENABLED_BY_DEFAULT.has(p.id) ? m.isDefault || i < 4 : false,
        isDefault: m.isDefault,
        responsibility: "official",
        multiplier: 1,
      }));
    const provider: Provider = {
      id: p.id,
      kind: "builtin",
      displayName: p.displayName,
      multiplier: p.id === "ark-agent-plan" ? 0 : 1,
      credentialCount: ENABLED_BY_DEFAULT.has(p.id) ? 1 : p.id === "openai" ? 2 : 0,
      mediaTypes: MEDIA(p.mediaTypes),
      discoverable: !["ark", "ark-agent-plan", "kling", "vidu"].includes(p.id),
      models,
    };
    return provider;
  });
}

function withExtras(providers: Provider[]): Provider[] {
  const ds = providers.find((p) => p.id === "dashscope")!;
  // 用户在内置供应商下添加的（官方未收录），由用户负责
  ds.models.push({ key: "dashscope:qwen-vl-max", entryId: "alibaba/qwen-vl-max", apiModelName: "qwen-vl-max", enabled: true, isDefault: false, responsibility: "user", multiplier: 1 });
  ds.discovered = [...ds.models.map((m) => m.apiModelName), "qwen3-coder-plus", "qwen-turbo", "qwen3-vl-plus", "wan2.5-i2v-preview"];
  const aistudio = providers.find((p) => p.id === "gemini-aistudio")!;
  aistudio.discovered = [...aistudio.models.map((m) => m.apiModelName).filter((n) => !n.startsWith("veo-3.1-lite")), "gemini-2.5-flash", "gemini-embedding-001"];
  return providers;
}

function customProviders(): Provider[] {
  return [
    {
      id: "c1",
      kind: "custom",
      displayName: "OpenRouter",
      baseUrl: "https://openrouter.ai/api/v1",
      multiplier: 1,
      credentialCount: 1,
      mediaTypes: ["text", "image", "video"],
      discoverable: true,
      models: [
        // OpenRouter 的 Anthropic 协议挂在 /api 下：base_url 写的是 /api/v1，消息请求覆盖为 /messages
        { key: "c1:anthropic/claude-sonnet-4.5", entryId: "anthropic/claude-sonnet-4-5", apiModelName: "anthropic/claude-sonnet-4.5", enabled: true, isDefault: true, responsibility: "user", endpoint: "anthropic-messages", multiplier: 1, pathOverrides: { messages: "/messages" } },
        { key: "c1:openai/gpt-5.4", entryId: "openai/gpt-5.4", apiModelName: "openai/gpt-5.4", enabled: true, isDefault: false, responsibility: "user", endpoint: "openai-chat", multiplier: 1 },
        { key: "c1:google/gemini-3.1-flash-image-preview", entryId: "google/gemini-3.1-flash-image-preview", apiModelName: "google/gemini-3.1-flash-image-preview", enabled: true, isDefault: true, responsibility: "user", endpoint: "openai-chat-image", multiplier: 1 },
        { key: "c1:google/veo-3.1-fast", entryId: "google/veo-3.1-fast-generate-001", apiModelName: "google/veo-3.1-fast", enabled: false, isDefault: false, responsibility: "user", endpoint: "openrouter-video", multiplier: 1 },
      ],
    },
    {
      id: "c2",
      kind: "custom",
      displayName: "智谱开放平台",
      baseUrl: "https://open.bigmodel.cn/api",
      multiplier: 1,
      credentialCount: 1,
      mediaTypes: ["text"],
      discoverable: true,
      models: [
        // 智谱 Chat 在 /api/paas/v4，Responses 在 /api/v1：base_url 只写到 /api，两条各自覆盖
        { key: "c2:glm-5.1", entryId: "zhipu/glm-5.1", apiModelName: "glm-5.1", enabled: true, isDefault: true, responsibility: "user", endpoint: "openai-chat", multiplier: 1, pathOverrides: { chat: "/paas/v4/chat/completions" } },
        { key: "c2:glm-5.1-responses", entryId: "zhipu/glm-5.1", apiModelName: "glm-5.1", enabled: false, isDefault: false, responsibility: "user", endpoint: "openai-responses", multiplier: 1, pathOverrides: { responses: "/v1/responses" } },
      ],
    },
    {
      id: "c3",
      kind: "custom",
      displayName: "星河中转",
      baseUrl: "https://api.xinghe-relay.example",
      multiplier: 0.8,
      credentialCount: 1,
      mediaTypes: ["text", "image", "video"],
      discoverable: true,
      models: [
        { key: "c3:claude-sonnet-4-5-20250929", entryId: "anthropic/claude-sonnet-4-5", apiModelName: "claude-sonnet-4-5-20250929", enabled: true, isDefault: false, responsibility: "user", endpoint: "anthropic-messages", multiplier: 1.2 },
        { key: "c3:doubao-seedance-2-0", entryId: "bytedance/doubao-seedance-2-0-260128", apiModelName: "doubao-seedance-2-0", enabled: true, isDefault: true, responsibility: "user", endpoint: "newapi-video", multiplier: 1 },
        {
          key: "c3:kling-v3",
          entryId: "kuaishou/kling-v3",
          apiModelName: "kling-v3",
          enabled: true,
          isDefault: false,
          responsibility: "user",
          endpoint: "newapi-video",
          multiplier: 1,
          capabilityOverride: { durations: [5, 10] },
          pathOverrides: { submit: "/kling/v1/videos/text2video", poll: "/kling/v1/videos/text2video/{task_id}" },
        },
        { key: "c3:flux-kontext-pro", entryId: "bfl/flux-kontext-pro", apiModelName: "flux-kontext-pro", enabled: true, isDefault: true, responsibility: "user", endpoint: "openai-images", multiplier: 1 },
        { key: "c3:xh-story-writer", entryId: null, apiModelName: "xh-story-writer", enabled: true, isDefault: true, responsibility: "user", endpoint: "openai-chat", multiplier: 1 },
      ],
    },
  ];
}

export function seedProviders(): Provider[] {
  return [...withExtras(builtinProviders()), ...customProviders()];
}
