/**
 * Global-setting configuration issues, each with sections consumed by `useSectionConfigIssues(section)`:
 * sidebars mark corresponding sections only; section-local `settings/ConfigIssueNotice` explains them, never global banners.
 * Unconfigured embedded Agents appear in ArcReel Agent sections only, excluded from `isComplete`, never activating lobby/header red dots.
 */
import { useMemo } from "react";
import { create } from "zustand";
import { API } from "@/api";
import type { SettingsSection } from "@/app-routes";
import { useEndpointCatalogStore } from "./endpoint-catalog-store";

// ---------------------------------------------------------------------------
// ConfigIssue
// ---------------------------------------------------------------------------

export interface ConfigIssue {
  key: string;
  /** Global-settings section owning issues. */
  section: SettingsSection;
  label: string;
  /** Explanatory i18n key: consequences and remedies. */
  description?: string;
}

async function getConfigStatus(): Promise<{
  issues: ConfigIssue[];
  availableMediaTypes: string[];
  isEmbeddedAgentConfigured: boolean;
}> {
  const issues: ConfigIssue[] = [];

  const [{ providers }, { providers: customProviders }, configRes] = await Promise.all([
    API.getProviders(),
    API.listCustomProviders(),
    API.getSystemConfig(),
  ]);

  // Check any provider supports each required media type.
  const readyProviders = providers.filter((p) => p.status === "ready");

  // Derive custom-provider endpoint→mediaType mappings from catalogs; fetch only when custom providers exist
  // (otherwise empty mappings are never read), avoiding pointless HTTP for existing users.
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

  // Audio is optional (narration only); absence never adds issues/red dots.
  // Expose availability through availableMediaTypes for generation-entry configure-audio-first notices.
  const availableMediaTypes = ["image", "video", "text", "audio"].filter(hasMediaType);

  return {
    issues,
    availableMediaTypes,
    isEmbeddedAgentConfigured: configRes.settings.anthropic_api_key?.is_set ?? false,
  };
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

interface ConfigStatusState {
  issues: ConfigIssue[];
  isEmbeddedAgentConfigured: boolean;
  /** Media types covered by currently ready providers, including custom providers. */
  availableMediaTypes: string[];
  isComplete: boolean;
  loading: boolean;
  initialized: boolean;
  /** Set when refresh() arrives during refreshes; run once more afterward to retain latest requested data. */
  pendingRefresh: boolean;
  /** Whether ready providers support media types (for example hasMediaType("audio")). */
  hasMediaType: (type: string) => boolean;
  fetch: () => Promise<void>;
  refresh: () => Promise<void>;
}

export const useConfigStatusStore = create<ConfigStatusState>((set, get) => {
  // One in-flight refresh chain, shared by concurrent refresh()/fetch() calls;
  // returned Promises resolve only after actual data persistence including follow-ups, so awaited state is current.
  let inflight: Promise<void> | null = null;

  const run = async (): Promise<void> => {
    // Loop until no new pending work; complete follow-ups in the same chain without resolving early.
    for (;;) {
      set({ loading: true, pendingRefresh: false });
      try {
        // react-doctor-disable-next-line react-doctor/async-await-in-loop -- follow-ups must await previous persistence before reading pendingRefresh; concurrency lets old responses overwrite new data
        const { issues, availableMediaTypes, isEmbeddedAgentConfigured } = await getConfigStatus();
        set({
          issues,
          availableMediaTypes,
          isEmbeddedAgentConfigured,
          isComplete: issues.length === 0,
          initialized: true,
        });
      } catch {
        // Failed refreshes become uninitialized and clear capabilities, preventing any consumer (even future callers not checking initialized)
        // from trusting stale successful data, while leaving subsequent fetch() retryable.
        set({ initialized: false, availableMediaTypes: [], isEmbeddedAgentConfigured: false });
      }
      // Set loading false only after entire chains terminate; retain true between follow-ups, avoiding true→false→true flicker.
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
      // Await existing in-flight refreshes instead of starting new rounds; otherwise refresh once.
      await (inflight ?? get().refresh());
    },

    refresh: () => {
      // Existing refreshes mark follow-up work and share completion chains; concurrent callers all await final persistence.
      // Otherwise post-save refresh() silently disappears into ongoing initial/previous refreshes,
      // leaving red dots/warnings at presave values.
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

/**
 * Section-local global-settings issues; absent sections return issues for every section.
 * Add unconfigured embedded Agents beyond `issues`: optional Agents are excluded from `isComplete`,
 * never activating lobby/header red dots, appearing only in ArcReel Agent sections/sidebar entries.
 */
export function useSectionConfigIssues(section?: SettingsSection): ConfigIssue[] {
  const issues = useConfigStatusStore((s) => s.issues);
  const agentMissing = useConfigStatusStore((s) => s.initialized && !s.isEmbeddedAgentConfigured);
  return useMemo(() => {
    const all = agentMissing ? [...issues, EMBEDDED_AGENT_ISSUE] : issues;
    return section ? all.filter((issue) => issue.section === section) : all;
  }, [issues, agentMissing, section]);
}
