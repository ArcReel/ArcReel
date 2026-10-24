import type { CustomProviderInfo, CustomProviderModelInfo } from "@/types";

/** 一个模型行对端点的使用：所属自定义供应商与模型。 */
export interface EndpointUsage {
  provider: CustomProviderInfo;
  model: CustomProviderModelInfo;
}

/** 按端点键分组自定义供应商的模型行；只有自定义供应商的模型能指定端点。 */
export function groupUsagesByEndpoint(providers: CustomProviderInfo[]): Map<string, EndpointUsage[]> {
  const byEndpoint = new Map<string, EndpointUsage[]>();
  for (const provider of providers) {
    for (const model of provider.models) {
      const list = byEndpoint.get(model.endpoint) ?? [];
      list.push({ provider, model });
      byEndpoint.set(model.endpoint, list);
    }
  }
  return byEndpoint;
}
