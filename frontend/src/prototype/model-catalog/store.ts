// PROTOTYPE — #3091 内存状态。所有保存、同步都只改这里，刷新页面即复原（「重置数据」同理）。

import { create } from "zustand";
import { effectiveOf, officialOf, type CatalogEntry, type EntryData, type Provider, type ProviderModel } from "./model";
import { FACTORY_VERSION, NEXT_VERSION, SYNCED_VERSION, nextDataset, seedEntries, seedProviders } from "./seed";

export type Variant = "A" | "B" | "C";
export const VARIANTS: Variant[] = ["A", "B", "C"];
export const VARIANT_NAME: Record<Variant, string> = {
  A: "独立的模型目录分区",
  B: "条目嵌在供应商里",
  C: "以模型为主轴",
};

export interface SyncChange {
  entryId: string;
  kind: "added" | "updated" | "deprecated" | "skipped";
  summary: string;
}

export interface SyncState {
  factoryVersion: string;
  /** 当前已应用的官方数据集版本。 */
  appliedVersion: string;
  lastCheckedAt: string;
  status: "ok" | "checking" | "offline";
  /** 最近一次应用带来的变化；skipped 是关了同步、被跳过的条目。 */
  lastChanges: SyncChange[];
}

const INITIAL_CHANGES: SyncChange[] = [
  { entryId: "anthropic/claude-sonnet-4-5", kind: "added", summary: "新收录" },
  { entryId: "kuaishou/kling-v3", kind: "updated", summary: "价格改为按分辨率分档" },
  { entryId: "kuaishou/kling-v3-omni", kind: "updated", summary: "价格改为按分辨率分档" },
  { entryId: "google/gemini-3.1-pro-preview", kind: "updated", summary: "按上下文档分段，新增缓存读价格" },
  { entryId: "deepseek/deepseek-v4-flash", kind: "updated", summary: "补上价格" },
  { entryId: "deepseek/deepseek-v4-pro", kind: "updated", summary: "补上价格" },
  { entryId: "openai/sora-2", kind: "deprecated", summary: "官方已下架" },
  { entryId: "openai/sora-2-pro", kind: "deprecated", summary: "官方已下架" },
  { entryId: "alibaba/wan2.7-t2v", kind: "skipped", summary: "官方调价；你改过这个条目，未应用" },
  { entryId: "anthropic/claude-haiku-4-5", kind: "skipped", summary: "官方收录了同名条目；你的条目仍由你负责，可打开同步改用官方数据" },
];

interface ProtoState {
  variant: Variant;
  entries: CatalogEntry[];
  providers: Provider[];
  sync: SyncState;
  setVariant: (v: Variant) => void;
  cycleVariant: (d: 1 | -1) => void;
  reset: () => void;
  setOffline: (offline: boolean) => void;
  /** 模拟「检查更新」：拿到下一版数据，整批应用，跳过关了同步的条目（ADR 0102）。 */
  checkForUpdates: () => void;

  /** 保存对条目的修改：从生效值复制一份整条副本，并自动关闭同步（ADR 0098）。 */
  saveEntry: (id: string, data: EntryData) => void;
  /** 打开同步即丢弃用户副本，等同「恢复官方值」。 */
  setEntrySync: (id: string, on: boolean) => void;
  createEntry: (data: EntryData) => void;

  updateProvider: (id: string, patch: Partial<Provider>) => void;
  updateProviderModel: (providerId: string, key: string, patch: Partial<ProviderModel>) => void;
  addProviderModel: (providerId: string, model: ProviderModel) => void;
  removeProviderModel: (providerId: string, key: string) => void;
  /** 同一媒体类型只能有一个默认。 */
  setDefault: (providerId: string, key: string) => void;
}

const VARIANT_KEY = "proto-3091-variant";
function initialVariant(): Variant {
  const fromUrl = new URLSearchParams(window.location.search).get("variant");
  if (fromUrl && (VARIANTS as string[]).includes(fromUrl)) return fromUrl as Variant;
  const stored = window.localStorage.getItem(VARIANT_KEY);
  return stored && (VARIANTS as string[]).includes(stored) ? (stored as Variant) : "A";
}

function initialSync(): SyncState {
  return { factoryVersion: FACTORY_VERSION, appliedVersion: SYNCED_VERSION, lastCheckedAt: "今天 09:12", status: "ok", lastChanges: INITIAL_CHANGES };
}

const mapProvider = (providers: Provider[], id: string, fn: (p: Provider) => Provider) => providers.map((p) => (p.id === id ? fn(p) : p));

export const useProto = create<ProtoState>((set, get) => ({
  variant: initialVariant(),
  entries: seedEntries(),
  providers: seedProviders(),
  sync: initialSync(),

  setVariant: (variant) => {
    window.localStorage.setItem(VARIANT_KEY, variant);
    set({ variant });
    syncVariantUrl(variant);
  },
  cycleVariant: (d) => {
    const i = VARIANTS.indexOf(get().variant);
    get().setVariant(VARIANTS[(i + d + VARIANTS.length) % VARIANTS.length]);
  },
  reset: () => set({ entries: seedEntries(), providers: seedProviders(), sync: initialSync() }),
  setOffline: (offline) => set((s) => ({ sync: { ...s.sync, status: offline ? "offline" : "ok" } })),
  checkForUpdates: () => {
    if (get().sync.status === "offline") return;
    set((s) => ({ sync: { ...s.sync, status: "checking" } }));
    window.setTimeout(() => {
      const { sync, entries } = get();
      if (sync.appliedVersion >= NEXT_VERSION) {
        set({ sync: { ...sync, status: "ok", lastCheckedAt: "刚刚" } });
        return;
      }
      const next = nextDataset();
      const changes: SyncChange[] = [];
      const updated = entries.map((e) => {
        const incoming = next.get(e.id);
        if (!incoming) return e;
        if (!e.syncOn) {
          changes.push({ entryId: e.id, kind: "skipped", summary: "官方更新了这个条目；你改过它，未应用" });
          return { ...e, synced: incoming };
        }
        changes.push({ entryId: e.id, kind: "updated", summary: e.id.includes("veo") ? "降价 25%" : "可选时长新增 15 秒" });
        return { ...e, synced: incoming };
      });
      set({ entries: updated, sync: { ...sync, status: "ok", appliedVersion: NEXT_VERSION, lastCheckedAt: "刚刚", lastChanges: changes } });
    }, 900);
  },

  saveEntry: (id, data) =>
    set((s) => ({
      entries: s.entries.map((e) => {
        if (e.id !== id) return e;
        // 新副本记下所基于的官方版本；用户新建的条目保持 user
        const basedOn = e.user?.version === "user" ? "user" : (officialOf(e)?.version ?? "user");
        return { ...e, user: { ...structuredClone(data), version: basedOn }, syncOn: false };
      }),
    })),
  setEntrySync: (id, on) =>
    set((s) => ({
      entries: s.entries.map((e) => {
        if (e.id !== id) return e;
        if (on) return officialOf(e) ? { ...e, syncOn: true, user: null } : e;
        // 手动关闭同步：以当前生效值建副本
        return { ...e, syncOn: false, user: e.user ?? { ...structuredClone(effectiveOf(e)), version: officialOf(e)?.version ?? "user" } };
      }),
    })),
  createEntry: (data) =>
    set((s) => ({ entries: [...s.entries, { id: data.id, factory: null, synced: null, user: { ...data, version: "user" }, syncOn: false }] })),

  updateProvider: (id, patch) => set((s) => ({ providers: mapProvider(s.providers, id, (p) => ({ ...p, ...patch })) })),
  updateProviderModel: (providerId, key, patch) =>
    set((s) => ({
      providers: mapProvider(s.providers, providerId, (p) => ({
        ...p,
        models: p.models.map((m) => {
          if (m.key !== key) return m;
          // 改挂端点时原有路径覆盖丢弃（ADR 0100）
          const endpointChanged = patch.endpoint && patch.endpoint !== m.endpoint;
          return { ...m, ...patch, ...(endpointChanged ? { pathOverrides: {} } : {}) };
        }),
      })),
    })),
  addProviderModel: (providerId, model) => set((s) => ({ providers: mapProvider(s.providers, providerId, (p) => ({ ...p, models: [...p.models, model] })) })),
  removeProviderModel: (providerId, key) =>
    set((s) => ({ providers: mapProvider(s.providers, providerId, (p) => ({ ...p, models: p.models.filter((m) => m.key !== key) })) })),
  setDefault: (providerId, key) =>
    set((s) => {
      const media = (m: ProviderModel) => {
        const e = s.entries.find((x) => x.id === m.entryId);
        return e ? effectiveOf(e).mediaType : null;
      };
      return {
        providers: mapProvider(s.providers, providerId, (p) => {
          const target = p.models.find((m) => m.key === key);
          const t = target ? media(target) : null;
          return { ...p, models: p.models.map((m) => (m.key === key ? { ...m, isDefault: true, enabled: true } : media(m) === t ? { ...m, isDefault: false } : m)) };
        }),
      };
    }),
}));

/** 设置页侧栏切换分区会改写 query，把原型参数补回去，变体可分享、刷新不丢。 */
export function syncVariantUrl(variant: Variant = useProto.getState().variant) {
  const url = new URL(window.location.href);
  if (url.searchParams.get("variant") === variant) return;
  url.searchParams.set("variant", variant);
  window.history.replaceState(window.history.state, "", url);
}

// ---------- 便捷选择器 ----------

export function useEntry(id: string | null | undefined): CatalogEntry | undefined {
  return useProto((s) => s.entries.find((e) => e.id === id));
}

/** 引用某个条目的全部供应商模型，跨内置与自定义供应商。 */
export function referencesOf(providers: Provider[], entryId: string): { provider: Provider; model: ProviderModel }[] {
  return providers.flatMap((provider) => provider.models.filter((m) => m.entryId === entryId).map((model) => ({ provider, model })));
}
