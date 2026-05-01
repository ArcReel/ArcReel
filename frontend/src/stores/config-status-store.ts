import { create } from "zustand";
import { API } from "@/api";
import { useEndpointCatalogStore } from "./endpoint-catalog-store";

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

  const [{ providers }, { providers: customProviders }, configRes] = await Promise.all([
    API.getProviders(),
    API.listCustomProviders(),
    API.getSystemConfig(),
  ]);

  const settings = configRes.settings;

  // 1. Check anthropic key
  if (!settings.anthropic_api_key?.is_set) {
    issues.push({
      key: "anthropic",
      tab: "agent",
      label: "agent_api_key_not_configured",
    });
  }

  // 2. Check any provider supports each media type
  const readyProviders = providers.filter((p) => p.status === "ready");

  // 自定义 provider 的 endpoint→mediaType 映射要从 catalog 派生：仅在有自定义 provider 时
  // 才需要 fetch（否则空映射也不会被读到，避免给已运行的旧用户加一次无谓 HTTP）。
  if (customProviders.length > 0) {
    await useEndpointCatalogStore.getState().fetch();
  }
  const endpointToMediaType = useEndpointCatalogStore.getState().endpointToMediaType;

  const hasMediaType = (type: string) => {
    // Check preset providers
    const hasPresetProvider = readyProviders.some((p) => p.media_types.includes(type));
    if (hasPresetProvider) return true;

    // Check custom providers for enabled models of this media type
    return customProviders.some((cp) =>
      cp.models.some((m) => endpointToMediaType[m.endpoint] === type && m.is_enabled)
    );
  };

  if (!hasMediaType("video")) {
    issues.push({
      key: "no-video-provider",
      tab: "providers",
      label: "video_provider_not_configured",
    });
  }
  if (!hasMediaType("image")) {
    issues.push({
      key: "no-image-provider",
      tab: "providers",
      label: "image_provider_not_configured",
    });
  }
  if (!hasMediaType("text")) {
    issues.push({
      key: "no-text-provider",
      tab: "providers",
      label: "text_provider_not_configured",
    });
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
