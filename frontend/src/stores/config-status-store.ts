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

async function getConfigStatus(): Promise<{ issues: ConfigIssue[]; availableMediaTypes: string[] }> {
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

  // audio 是可选能力（仅说书旁白用），缺失不进 issues 红点；
  // 可用性经 availableMediaTypes 暴露给生成入口做"请先配置 audio 供应商"前置提示。
  const availableMediaTypes = ["image", "video", "text", "audio"].filter(hasMediaType);

  return { issues, availableMediaTypes };
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

interface ConfigStatusState {
  issues: ConfigIssue[];
  /** 当前已就绪供应商（含自定义）覆盖到的媒体类型集合。 */
  availableMediaTypes: string[];
  isComplete: boolean;
  loading: boolean;
  initialized: boolean;
  /** 进行中刷新期间又收到 refresh() 时置位,本轮完成后补跑一次,避免丢掉最后一次请求的数据。 */
  pendingRefresh: boolean;
  /** 是否有就绪供应商支持该媒体类型（如 hasMediaType("audio")）。 */
  hasMediaType: (type: string) => boolean;
  fetch: () => Promise<void>;
  refresh: () => Promise<void>;
}

export const useConfigStatusStore = create<ConfigStatusState>((set, get) => {
  // 单条在途刷新链:并发 refresh()/fetch() 都复用它,
  // 返回的 Promise 只在数据(含补跑)真正落地后才 resolve——await 完即代表状态最新。
  let inflight: Promise<void> | null = null;

  const run = async (): Promise<void> => {
    // 循环直到没有新的 pending:补跑在同一条链上完成,而非提前 resolve。
    for (;;) {
      set({ loading: true, pendingRefresh: false });
      try {
        const { issues, availableMediaTypes } = await getConfigStatus();
        set({ issues, availableMediaTypes, isComplete: issues.length === 0, initialized: true });
      } catch {
        // 失败回退未初始化并清空能力集：避免任何消费方（含未来不检查 initialized 的调用方）
        // 把上一次成功的过期数据当作可信，同时让下次 fetch() 仍可重试。
        set({ initialized: false, availableMediaTypes: [] });
      }
      // loading 仅在整条链终止时才置 false:补跑间隙保持 true,避免 true→false→true 闪烁。
      if (!get().pendingRefresh) {
        set({ loading: false });
        break;
      }
    }
  };

  return {
    issues: [],
    availableMediaTypes: [],
    isComplete: true,
    loading: false,
    initialized: false,
    pendingRefresh: false,

    hasMediaType: (type: string) => get().availableMediaTypes.includes(type),

    fetch: async () => {
      if (get().initialized) return;
      // 已有刷新在途:等它,而不是另起一轮;无在途则发起一次。
      await (inflight ?? get().refresh());
    },

    refresh: () => {
      // 已有刷新在途:标记补跑并复用同一条完成链,所有并发调用方都 await 到最终落地。
      // 否则"保存供应商后的 refresh()"会被进行中的初始加载/前一次刷新静默吞掉,
      // 红点/警示停留在保存前的旧值。
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
