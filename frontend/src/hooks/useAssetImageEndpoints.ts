import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import type { ModelCandidatesResponse, SystemConfigSettings } from "@/types/system";
import {
  resolveAssetImageEndpoint,
  type AssetImageEndpointType,
  type ResolvedAssetImageEndpoint,
} from "@/utils/asset-image-endpoint";

export interface AssetImageEndpoints {
  /** 全部图像端点候选（默认层全量），"provider/model" 形态。 */
  options: string[];
  providerNames: Record<string, string>;
  modelNames: Record<string, string>;
  /** 未覆盖时本次生成会执行的端点及其来源层。 */
  current: ResolvedAssetImageEndpoint;
  loading: boolean;
  error: boolean;
  reload: () => Promise<void>;
}

/**
 * 图像端点候选与全局设置是全局数据，不随资产变化，但工作室每次切资产都会重挂。按语言缓存
 * 一份短 TTL 快照：切资产时命中则首帧即有值，下拉不闪「加载中」。
 */
interface CatalogSnapshot {
  language: string;
  candidates: ModelCandidatesResponse;
  settings: SystemConfigSettings;
  fetchedAt: number;
}
let catalogSnapshot: CatalogSnapshot | null = null;
const CATALOG_TTL_MS = 30_000;

function snapshotFor(language: string): CatalogSnapshot | null {
  const cached = catalogSnapshot;
  if (!cached || cached.language !== language || Date.now() - cached.fetchedAt >= CATALOG_TTL_MS) return null;
  return cached;
}

export function useAssetImageEndpoints(assetType: AssetImageEndpointType, useImageToImage: boolean): AssetImageEndpoints {
  const { i18n } = useTranslation();
  const language = i18n.language;
  const project = useProjectsStore((s) => s.currentProjectData);
  // 命中缓存时首帧即回填，避免整屏工作室每次切资产都闪一下「加载中」
  const [candidates, setCandidates] = useState<ModelCandidatesResponse | null>(
    () => snapshotFor(language)?.candidates ?? null,
  );
  const [settings, setSettings] = useState<SystemConfigSettings | null>(() => snapshotFor(language)?.settings ?? null);
  const [error, setError] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const loading = candidates === null && !error;

  const reload = useCallback(async () => {
    if (snapshotFor(language)) return;
    // 接管方轮换 controller（见 docs/standards/frontend-async.md）：重试可能在上一轮
    // 请求尚未回来时触发，旧响应回来时已经过期。
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const [nextCandidates, config] = await Promise.all([
        API.getModelCandidates({ signal: controller.signal }),
        API.getSystemConfig({ signal: controller.signal }),
      ]);
      if (controller.signal.aborted) return;
      catalogSnapshot = { language, candidates: nextCandidates, settings: config.settings, fetchedAt: Date.now() };
      setCandidates(nextCandidates);
      setSettings(config.settings);
      setError(false);
    } catch {
      if (!controller.signal.aborted) {
        setError(true);
      }
    }
  }, [language]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 挂载时拉一次（未命中缓存才请求），属于受控的初始化加载
    void reload();
    return () => abortRef.current?.abort();
  }, [reload]);

  const current = useMemo(
    () => resolveAssetImageEndpoint({ assetType, useImageToImage, project, globalSettings: settings }),
    [assetType, useImageToImage, project, settings],
  );

  return {
    options: candidates?.image.default ?? [],
    providerNames: candidates?.provider_names ?? {},
    modelNames: candidates?.model_names ?? {},
    current,
    loading,
    error,
    reload,
  };
}
