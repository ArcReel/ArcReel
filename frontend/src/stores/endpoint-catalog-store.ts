import { create } from "zustand";
import { API } from "@/api";
import type { EndpointDescriptor, ImageCap, MediaType } from "@/types";


export interface EndpointPath {
  method: string;
  path: string;
}

export interface EndpointConstraints {
  sizeFixed: boolean;
  durationFixed: boolean;
  durationFrameRateMissing: boolean;
  durationTierEmpty: boolean;
  nativeResolution: string | null;
}

interface EndpointCatalogState {
  endpoints: EndpointDescriptor[];
  endpointToMediaType: Record<string, MediaType>;
  endpointPaths: Record<string, EndpointPath>;
  endpointToImageCapabilities: Record<string, ImageCap[]>;
  endpointToEndImageCapable: Record<string, boolean>;
  endpointConstraints: Record<string, EndpointConstraints>;
  loading: boolean;
  initialized: boolean;
  fetch: () => Promise<void>;
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
      set({ loading: false });
    }
  },
}));
