import { create } from "zustand";
import { API } from "@/api";

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
      label: "ArcReel Agent API Key (Anthropic) not configured",
    });
  }

  // 2. Check any provider supports each media type
  const readyProviders = providers.filter((p) => p.status === "ready");

  const hasMediaType = (type: string) =>
    readyProviders.some((p) => p.media_types.includes(type));

  if (!hasMediaType("video")) {
    issues.push({
      key: "no-video-provider",
      tab: "providers",
      label: "No provider configured for video generation",
    });
  }
  if (!hasMediaType("image")) {
    issues.push({
      key: "no-image-provider",
      tab: "providers",
      label: "No provider configured for image generation",
    });
  }
  if (!hasMediaType("text")) {
    issues.push({
      key: "no-text-provider",
      tab: "providers",
      label: "No provider configured for text generation",
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
