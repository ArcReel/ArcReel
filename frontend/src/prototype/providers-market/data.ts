// PROTOTYPE — #2972 的数据：预置供应商、端点目录、市场源与官方服务取真实接口；
// 自定义供应商取真实接口后再拼上样例。所有写操作都是桩：草稿只在内存里，保存只弹提示。

import { useEffect, useState, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type { EndpointDescriptor, ProviderInfo } from "@/types";
import { SAMPLE_CUSTOM_PROVIDERS, fromRealCustomProvider, type ProtoCustomProvider } from "./samples";
import { useProto } from "./store";

interface Catalog {
  presets: ProviderInfo[];
  custom: ProtoCustomProvider[];
  loaded: boolean;
}

let catalog: Catalog = { presets: [], custom: [], loaded: false };
let started = false;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

function load() {
  if (started) return;
  started = true;
  void Promise.all([API.getProviders(), API.listCustomProviders()]).then(([p, c]) => {
    catalog = { presets: p.providers, custom: c.providers.map(fromRealCustomProvider), loaded: true };
    emit();
  });
}

/** 已保存的自定义供应商（草稿之外的「真相」）；原型的保存会改写这里。 */
const savedOverrides = new Map<number, ProtoCustomProvider>();

export function commitCustomProvider(p: ProtoCustomProvider) {
  savedOverrides.set(p.id, structuredClone(p));
  catalog = { ...catalog };
  emit();
}

export function useCatalog() {
  useEffect(load, []);
  const c = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => catalog,
  );
  const { samples } = useProto();
  const custom = [...c.custom, ...(samples ? SAMPLE_CUSTOM_PROVIDERS : [])].map((p) => savedOverrides.get(p.id) ?? p);
  return { presets: c.presets, custom, loaded: c.loaded };
}

export function useEndpoints() {
  const endpoints = useEndpointCatalogStore((s) => s.endpoints);
  const fetch = useEndpointCatalogStore((s) => s.fetch);
  useEffect(() => {
    void fetch();
  }, [fetch]);
  return endpoints;
}

export function useEndpointLabel() {
  const { t } = useTranslation("dashboard");
  return (e: EndpointDescriptor | undefined, fallback = "未选择") => (e ? (e.display_name ?? t(e.display_name_key)) : fallback);
}

export const MEDIA_LABEL: Record<string, string> = { text: "文本", image: "图片", video: "视频", audio: "音频" };

export function useOnce<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [v, setV] = useState<T | null>(null);
  useEffect(() => {
    let alive = true;
    void fn().then((r) => alive && setV(r));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 原型
  }, deps);
  return v;
}
