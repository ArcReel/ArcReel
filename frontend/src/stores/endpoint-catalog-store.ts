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

/** 一个端点对尺寸与时长这两维的约束。只有 ComfyUI 端点会取非默认值（docs/adr/0082）。 */
export interface EndpointConstraints {
  sizeFixed: boolean;
  /** 只决定时长只读态的文案：workflow 天生时长固定，还是它没提供帧率。 */
  durationFixed: boolean;
  /** 时长这一维给不出任何档位——档位编辑区只读、项目页的时长控件不渲染，判据取这一位。 */
  durationTierEmpty: boolean;
  nativeResolution: string | null;
}

interface EndpointCatalogState {
  endpoints: EndpointDescriptor[];
  /** key → media_type，组件层不再每次重 derive。 */
  endpointToMediaType: Record<string, MediaType>;
  /** key → { method, path }，给 EndpointSelect 显示路径前缀。 */
  endpointPaths: Record<string, EndpointPath>;
  /** key → image capability 数组（仅 image 类 endpoint 有，非 image 不出现在 map 中）。 */
  endpointToImageCapabilities: Record<string, ImageCap[]>;
  /** key → 执行层是否下传尾帧约束（仅 video 类为 true）；决定 last_frame 覆盖能否强制开启。 */
  endpointToEndImageCapable: Record<string, boolean>;
  /** key → 参数约束四项：尺寸 / 时长这两维该端点驱不驱动得了、档位给不给得出来，以及不选档位时的原生分辨率。 */
  endpointConstraints: Record<string, EndpointConstraints>;
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
      // 失败时保持 initialized=false，组件可降级显示 placeholder；下次 fetch 仍会重试。
      set({ loading: false });
    }
  },
}));
