import { useMemo } from "react";
import { create } from "zustand";
import { API } from "@/api";
import type { SettingsSection } from "@/app-routes";
import { useEndpointCatalogStore } from "./endpoint-catalog-store";


export interface ConfigIssue {
  key: string;
  section: SettingsSection;
  label: string;
  description?: string;
}

async function getConfigStatus(): Promise<{
  issues: ConfigIssue[];
  availableMediaTypes: string[];
  isEmbeddedAgentConfigured: boolean;
}> {
  const issues: ConfigIssue[] = [];

  const [{ providers }, { providers: customProviders }, configRes, autoBackends] = await Promise.all([
    API.getProviders(),
    API.listCustomProviders(),
    API.getSystemConfig(),
    API.getAutoBackends().catch(() => null),
  ]);

  const readyProviders = providers.filter((p) => p.status === "ready");

  if (customProviders.length > 0) {
    await useEndpointCatalogStore.getState().fetch();
  }
  const endpointToMediaType = useEndpointCatalogStore.getState().endpointToMediaType;

  const hasMediaType = (type: string) => {
    const hasPresetProvider = readyProviders.some((p) => p.media_types.includes(type));
    if (hasPresetProvider) return true;

    return customProviders.some((cp) =>
      cp.models.some((m) => endpointToMediaType[m.endpoint] === type && m.is_enabled)
    );
  };

  if (!hasMediaType("video")) {
    issues.push({
      key: "no-video-provider",
      section: "providers",
      label: "video_provider_not_configured",
    });
  }
  if (!hasMediaType("image")) {
    issues.push({
      key: "no-image-provider",
      section: "providers",
      label: "image_provider_not_configured",
    });
  }
  if (!hasMediaType("text")) {
    issues.push({
      key: "no-text-provider",
      section: "providers",
      label: "text_provider_not_configured",
    });
  }

  const settings = configRes.settings;
  const defaultModels = [
    {
      type: "video",
      layers: [settings.default_video_backend, settings.default_video_backend_i2v, settings.default_video_backend_r2v],
      auto: [autoBackends?.i2v, autoBackends?.r2v],
    },
    {
      type: "image",
      layers: [settings.default_image_backend, settings.default_image_backend_t2i, settings.default_image_backend_i2i],
      auto: [autoBackends?.t2i, autoBackends?.i2i],
    },
    {
      type: "text",
      layers: [settings.default_text_backend, settings.text_backend_simple, settings.text_backend_complex],
      auto: [autoBackends?.text, autoBackends?.text],
    },
  ] as const;
  const isSet = (value: string | undefined) => !!value?.includes("/");
  for (const { type, layers, auto } of defaultModels) {
    const [base, ...subs] = layers;
    const covered = subs.every((sub, index) => isSet(sub) || isSet(base) || !!auto[index]);
    if (autoBackends && hasMediaType(type) && !covered) {
      issues.push({
        key: `no-default-${type}-model`,
        section: "default-models",
        label: `${type}_default_model_not_set`,
        description: "default_model_not_set_desc",
      });
    }
  }

  const availableMediaTypes = ["image", "video", "text", "audio"].filter(hasMediaType);

  return {
    issues,
    availableMediaTypes,
    isEmbeddedAgentConfigured: configRes.settings.anthropic_api_key?.is_set ?? false,
  };
}


interface ConfigStatusState {
  issues: ConfigIssue[];
  isEmbeddedAgentConfigured: boolean;
  availableMediaTypes: string[];
  isComplete: boolean;
  loading: boolean;
  initialized: boolean;
  pendingRefresh: boolean;
  hasMediaType: (type: string) => boolean;
  fetch: () => Promise<void>;
  refresh: () => Promise<void>;
}

export const useConfigStatusStore = create<ConfigStatusState>((set, get) => {
  let inflight: Promise<void> | null = null;

  const run = async (): Promise<void> => {
    for (;;) {
      set({ loading: true, pendingRefresh: false });
      try {
        // react-doctor-disable-next-line react-doctor/async-await-in-loop -- 补跑必须等上一轮落地后再读 pendingRefresh，并发会让旧响应覆盖新数据
        const { issues, availableMediaTypes, isEmbeddedAgentConfigured } = await getConfigStatus();
        set({
          issues,
          availableMediaTypes,
          isEmbeddedAgentConfigured,
          isComplete: issues.length === 0,
          initialized: true,
        });
      } catch {
        set({ initialized: false, availableMediaTypes: [], isEmbeddedAgentConfigured: false });
      }
      if (!get().pendingRefresh) {
        set({ loading: false });
        break;
      }
    }
  };

  return {
    issues: [],
    isEmbeddedAgentConfigured: false,
    availableMediaTypes: [],
    isComplete: true,
    loading: false,
    initialized: false,
    pendingRefresh: false,

    hasMediaType: (type: string) => get().availableMediaTypes.includes(type),

    fetch: async () => {
      if (get().initialized) return;
      await (inflight ?? get().refresh());
    },

    refresh: () => {
      if (inflight) {
        set({ pendingRefresh: true });
        return inflight;
      }
      inflight = run().finally(() => {
        inflight = null;
      });
      return inflight;
    },
  };
});

const EMBEDDED_AGENT_ISSUE: ConfigIssue = {
  key: "no-embedded-agent",
  section: "arcreel-agent",
  label: "embedded_agent_not_configured",
  description: "embedded_agent_not_configured_desc",
};

export function useSectionConfigIssues(section?: SettingsSection): ConfigIssue[] {
  const issues = useConfigStatusStore((s) => s.issues);
  const agentMissing = useConfigStatusStore((s) => s.initialized && !s.isEmbeddedAgentConfigured);
  return useMemo(() => {
    const all = agentMissing ? [...issues, EMBEDDED_AGENT_ISSUE] : issues;
    return section ? all.filter((issue) => issue.section === section) : all;
  }, [issues, agentMissing, section]);
}
