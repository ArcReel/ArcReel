import { useMemo } from "react";
import { catalogDisplayNames, type CatalogDisplayNames } from "@/utils/provider-models";
import type { ModelCandidatesResponse, SystemConfigOptions } from "@/types/system";
import type { ProviderInfo } from "@/types/provider";

/**
 * 下拉里显示的供应商名与模型名，三层叠加、由旧到新：目录（`/providers`，含未 ready 的供应商，
 * 兜住已失去凭证却仍是生效值的条目）→ options（整页配置，随保存重取）→ 候选（随语言重取）。
 *
 * 语言切换只重取最上层的候选，不重取整页配置——后者会连带清空表单里未保存的编辑。
 */
export function useDisplayNames(
  providers: ProviderInfo[],
  options: Pick<SystemConfigOptions, "provider_names" | "model_names"> | null,
  candidates: ModelCandidatesResponse | null,
): CatalogDisplayNames {
  const catalog = useMemo(() => catalogDisplayNames(providers), [providers]);
  return useMemo(
    () => ({
      providerNames: {
        ...catalog.providerNames,
        ...(options?.provider_names ?? {}),
        ...(candidates?.provider_names ?? {}),
      },
      modelNames: {
        ...catalog.modelNames,
        ...(options?.model_names ?? {}),
        ...(candidates?.model_names ?? {}),
      },
    }),
    [catalog, options, candidates],
  );
}
