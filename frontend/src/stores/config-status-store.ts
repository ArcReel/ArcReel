import { create } from "zustand";
import { API } from "@/api";
import type { SystemBackend, SystemConfigView } from "@/types";

// ---------------------------------------------------------------------------
// ConfigIssue
// ---------------------------------------------------------------------------

export interface ConfigIssue {
  key: string;
  tab: "agent" | "media";
  label: string;
}

function checkBackendCredential(backend: SystemBackend, config: SystemConfigView): boolean {
  return backend === "aistudio" ? config.gemini_api_key.is_set : config.vertex_credentials.is_set;
}

export function getConfigIssues(config: SystemConfigView): ConfigIssue[] {
  const issues: ConfigIssue[] = [];
  if (!config.anthropic_api_key.is_set) {
    issues.push({
      key: "anthropic",
      tab: "agent",
      label: "ArcReel 智能体 API Key（Anthropic）未配置",
    });
  }
  const imageCredentialMissing = !checkBackendCredential(config.image_backend, config);
  const videoCredentialMissing = !checkBackendCredential(config.video_backend, config);
  const sharedMediaBackendMissing =
    imageCredentialMissing &&
    videoCredentialMissing &&
    config.image_backend === config.video_backend;

  if (sharedMediaBackendMissing) {
    issues.push({
      key: `media-${config.image_backend}`,
      tab: "media",
      label:
        config.image_backend === "aistudio"
          ? "AI 生图/生视频 API Key（Gemini AI Studio）未配置"
          : "AI 生图/生视频 Vertex AI 凭证未上传",
    });
    return issues;
  }

  if (imageCredentialMissing) {
    issues.push({
      key: "image",
      tab: "media",
      label:
        config.image_backend === "aistudio"
          ? "AI 生图 API Key（Gemini AI Studio）未配置"
          : "AI 生图 Vertex AI 凭证未上传",
    });
  }

  if (videoCredentialMissing) {
    issues.push({
      key: "video",
      tab: "media",
      label:
        config.video_backend === "aistudio"
          ? "AI 生视频 API Key（Gemini AI Studio）未配置"
          : "AI 生视频 Vertex AI 凭证未上传",
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
      const res = await API.getSystemConfig();
      const issues = getConfigIssues(res.config);
      set({ issues, isComplete: issues.length === 0, loading: false, initialized: true });
    } catch {
      set({ loading: false });
    }
  },
}));
