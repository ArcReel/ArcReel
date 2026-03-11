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

function dedupIssues(issues: ConfigIssue[]): ConfigIssue[] {
  const mediaIssues = issues.filter((i) => i.tab === "media");
  if (mediaIssues.length === 2) {
    const [img, vid] = mediaIssues;
    if (img.label === vid.label) {
      // Merge identical image & video issues
      const mergedLabel = img.label
        .replace("AI 生图 ", "AI 生图/生视频 ")
        .replace("AI 生视频 ", "AI 生图/生视频 ");
      const merged: ConfigIssue = { key: "media", tab: "media", label: mergedLabel };
      const agentIssue = issues.find((i) => i.tab === "agent");
      return agentIssue ? [agentIssue, merged] : [merged];
    }
  }
  return issues;
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
  if (!checkBackendCredential(config.image_backend, config)) {
    issues.push({
      key: "image",
      tab: "media",
      label:
        config.image_backend === "aistudio"
          ? "AI 生图 API Key（Gemini AI Studio）未配置"
          : "AI 生图 Vertex AI 凭证未上传",
    });
  }
  if (!checkBackendCredential(config.video_backend, config)) {
    issues.push({
      key: "video",
      tab: "media",
      label:
        config.video_backend === "aistudio"
          ? "AI 生视频 API Key（Gemini AI Studio）未配置"
          : "AI 生视频 Vertex AI 凭证未上传",
    });
  }
  return dedupIssues(issues);
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
    if (get().initialized) return;
    await get().refresh();
  },

  refresh: async () => {
    set({ loading: true });
    try {
      const res = await API.getSystemConfig();
      const issues = getConfigIssues(res.config);
      set({ issues, isComplete: issues.length === 0, loading: false, initialized: true });
    } catch {
      set({ loading: false, initialized: true });
    }
  },
}));
