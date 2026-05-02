import { create } from "zustand";
import { API } from "@/api";
import type { EndpointDescriptor, ImageCap, MediaType } from "@/types";

// ---------------------------------------------------------------------------
// EndpointCatalog —— 自定义供应商 endpoint 元数据的 FE 端缓存。
// 真相源在后端 lib/custom_provider/endpoints.py:ENDPOINT_REGISTRY，
// 由 GET /api/v1/custom-providers/endpoints 拉取，FE 不再硬编码 endpoint 列表/路径/媒体类型 map。
// ---------------------------------------------------------------------------

export interface EndpointPath {
  method: string;
  path: string;
}

interface EndpointCatalogState {
  endpoints: EndpointDescriptor[];
  /** key → media_type，组件层不再每次重 derive。 */
  endpointToMediaType: Record<string, MediaType>;
  /** key → { method, path }，给 EndpointSelect 显示路径前缀。 */
  endpointPaths: Record<string, EndpointPath>;
  /** key → image capability 数组（仅 image 类 endpoint 有，非 image 不出现在 map 中）。 */
  endpointToImageCapabilities: Record<string, ImageCap[]>;
  loading: boolean;
  initialized: boolean;
  /** 短路：已初始化或加载中 → 直接 return；否则触发一次 refresh。 */
  fetch: () => Promise<void>;
  /** 强制刷新（设置页主动重拉时用）。 */
  refresh: () => Promise<void>;
}

function deriveMaps(endpoints: EndpointDescriptor[]): {
  endpointToMediaType: Record<string, MediaType>;
  endpointPaths: Record<string, EndpointPath>;
  endpointToImageCapabilities: Record<string, ImageCap[]>;
} {
  const endpointToMediaType: Record<string, MediaType> = {};
  const endpointPaths: Record<string, EndpointPath> = {};
  const endpointToImageCapabilities: Record<string, ImageCap[]> = {};
  for (const e of endpoints) {
    endpointToMediaType[e.key] = e.media_type;
    endpointPaths[e.key] = { method: e.request_method, path: e.request_path_template };
    if (e.image_capabilities) {
      endpointToImageCapabilities[e.key] = e.image_capabilities;
    }
  }
  return { endpointToMediaType, endpointPaths, endpointToImageCapabilities };
}

export const useEndpointCatalogStore = create<EndpointCatalogState>((set, get) => ({
  endpoints: [],
  endpointToMediaType: {},
  endpointPaths: {},
  endpointToImageCapabilities: {},
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
      const { endpointToMediaType, endpointPaths, endpointToImageCapabilities } = deriveMaps(res.endpoints);
      set({
        endpoints: res.endpoints,
        endpointToMediaType,
        endpointPaths,
        endpointToImageCapabilities,
        loading: false,
        initialized: true,
      });
    } catch {
      // 失败时保持 initialized=false，组件可降级显示 placeholder；下次 fetch 仍会重试。
      set({ loading: false });
    }
  },
}));
