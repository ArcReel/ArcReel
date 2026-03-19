import { create } from "zustand";
import { API } from "@/api";
import type { ProviderInfo } from "@/types";

// ---------------------------------------------------------------------------
// ConfigIssue
// ---------------------------------------------------------------------------

export interface ConfigIssue {
  key: string;
  tab: "agent" | "providers" | "media" | "usage";
  label: string;
}

async function getConfigIssues(): Promise<ConfigIssue[]> {
  const issues: ConfigIssue[] = [];

  const [{ providers }, configRes] = await Promise.all([
    API.getProviders(),
    API.getSystemConfig(),
  ]);

  const settings = configRes.settings;

  // 1. Check anthropic key
  if (!settings.anthropic_api_key?.is_set) {
    issues.push({
      key: "anthropic",
      tab: "agent",
      label: "ArcReel 智能体 API Key（Anthropic）未配置",
    });
  }

  // 2. Check default backends are selected
  const videoBackend = settings.default_video_backend || "";
  const imageBackend = settings.default_image_backend || "";

  if (!videoBackend) {
    issues.push({
      key: "no-video-backend",
      tab: "media",
      label: "未选择默认视频模型",
    });
  }
  if (!imageBackend) {
    issues.push({
      key: "no-image-backend",
      tab: "media",
      label: "未选择默认图片模型",
    });
  }

  // 3. Check default backends' providers are ready
  const videoProvider = videoBackend.split("/")[0];
  const imageProvider = imageBackend.split("/")[0];

  const checked = new Set<string>();

  for (const [providerName, label] of [
    [videoProvider, "视频"],
    [imageProvider, "图片"],
  ] as [string, string][]) {
    if (!providerName || checked.has(providerName)) continue;
    checked.add(providerName);

    const pInfo: ProviderInfo | undefined = providers.find(
      (p) => p.id === providerName,
    );
    if (pInfo && pInfo.status !== "ready") {
      issues.push({
        key: `provider-${pInfo.id}`,
        tab: "providers",
        label: `默认${label}供应商 ${pInfo.display_name} 未配置完成`,
      });
    }
  }

  return issues;
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

interface ConfigStatusState {
  issues: ConfigIssue[];
  isComplete: boolean;
  loading: boolean;
  initialized: boolean;
  fetch: () => Promise<void>;
  refresh: () => Promise<void>;
}

export const useConfigStatusStore = create<ConfigStatusState>((set, get) => ({
  issues: [],
  isComplete: true,
  loading: false,
  initialized: false,

  fetch: async () => {
    if (get().initialized || get().loading) return;
    await get().refresh();
  },

  refresh: async () => {
    if (get().loading) return;
    set({ loading: true });
    try {
      const issues = await getConfigIssues();
      set({ issues, isComplete: issues.length === 0, loading: false, initialized: true });
    } catch {
      set({ loading: false });
    }
  },
}));
