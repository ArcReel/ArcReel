import { create } from "zustand";
import { API } from "@/api";
import type { EndpointDescriptor, ImageCap, MediaType } from "@/types";

// ---------------------------------------------------------------------------
// EndpointCatalog: frontend cache of custom-provider endpoint metadata.
// Truth resides in backend lib/custom_provider/endpoints.py:ENDPOINT_REGISTRY,
// fetched through GET /api/v1/custom-providers/endpoints; frontend no longer hardcodes endpoint lists/paths/media-type maps.
// ---------------------------------------------------------------------------

export interface EndpointPath {
  method: string;
  path: string;
}

/** Endpoint constraints for dimensions/duration. Only ComfyUI endpoints use nondefault values (docs/adr/0082). */
export interface EndpointConstraints {
  sizeFixed: boolean;
  /** Duration-readonly copy only: workflows inherently have fixed durations (frames unbound). */
  durationFixed: boolean;
  /** Also copy only: empty tiers result from unreadable frame-rate sources. Both false means frame rates exist but yield no whole-second tiers. */
  durationFrameRateMissing: boolean;
  /** No tiers for duration dimensions: tier editors readonly, project duration controls hidden; use this predicate. */
  durationTierEmpty: boolean;
  nativeResolution: string | null;
}

interface EndpointCatalogState {
  endpoints: EndpointDescriptor[];
  /** key → media_type, without repeated component-layer derivation. */
  endpointToMediaType: Record<string, MediaType>;
  /** key → { method, path }, for EndpointSelect path prefixes. */
  endpointPaths: Record<string, EndpointPath>;
  /** key → image-capability arrays (image endpoints only; nonimage endpoints absent from maps). */
  endpointToImageCapabilities: Record<string, ImageCap[]>;
  /** key → execution forwards ending-frame constraints (video only true); determines whether last_frame overrides can force enablement. */
  endpointToEndImageCapable: Record<string, boolean>;
  /** key → four constraints: endpoint control of dimensions/duration, available tiers, and native resolutions without selected tiers. */
  endpointConstraints: Record<string, EndpointConstraints>;
  loading: boolean;
  initialized: boolean;
  /** Short-circuit initialized/loading → return directly; otherwise refresh once. */
  fetch: () => Promise<void>;
  /** Force refresh for settings-page explicit refetches. */
  refresh: () => Promise<void>;
}

function deriveMaps(endpoints: EndpointDescriptor[]): {
  endpointToMediaType: Record<string, MediaType>;
  endpointPaths: Record<string, EndpointPath>;
  endpointToImageCapabilities: Record<string, ImageCap[]>;
  endpointToEndImageCapable: Record<string, boolean>;
  endpointConstraints: Record<string, EndpointConstraints>;
} {
  const endpointToMediaType: Record<string, MediaType> = {};
  const endpointPaths: Record<string, EndpointPath> = {};
  const endpointToImageCapabilities: Record<string, ImageCap[]> = {};
  const endpointToEndImageCapable: Record<string, boolean> = {};
  const endpointConstraints: Record<string, EndpointConstraints> = {};
  for (const e of endpoints) {
    endpointToMediaType[e.key] = e.media_type;
    endpointPaths[e.key] = { method: e.request_method, path: e.request_path_template };
    if (e.image_capabilities) {
      endpointToImageCapabilities[e.key] = e.image_capabilities;
    }
    endpointToEndImageCapable[e.key] = e.end_image_capable;
    endpointConstraints[e.key] = {
      sizeFixed: e.size_fixed,
      durationFixed: e.duration_fixed,
      durationFrameRateMissing: e.duration_frame_rate_missing,
      durationTierEmpty: e.duration_tier_empty,
      nativeResolution: e.native_resolution,
    };
  }
  return {
    endpointToMediaType,
    endpointPaths,
    endpointToImageCapabilities,
    endpointToEndImageCapable,
    endpointConstraints,
  };
}

export const useEndpointCatalogStore = create<EndpointCatalogState>((set, get) => ({
  endpoints: [],
  endpointToMediaType: {},
  endpointPaths: {},
  endpointToImageCapabilities: {},
  endpointToEndImageCapable: {},
  endpointConstraints: {},
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
      const res = await API.listEndpointCatalog();
      const {
        endpointToMediaType,
        endpointPaths,
        endpointToImageCapabilities,
        endpointToEndImageCapable,
        endpointConstraints,
      } = deriveMaps(res.endpoints);
      set({
        endpoints: res.endpoints,
        endpointToMediaType,
        endpointPaths,
        endpointToImageCapabilities,
        endpointToEndImageCapable,
        endpointConstraints,
        loading: false,
        initialized: true,
      });
    } catch {
      // Retain initialized=false on failure; components may show placeholders and the next fetch still retries.
      set({ loading: false });
    }
  },
}));
