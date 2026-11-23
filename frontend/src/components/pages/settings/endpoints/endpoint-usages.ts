import type { CustomProviderInfo, CustomProviderModelInfo } from "@/types";

export interface EndpointUsage {
  provider: CustomProviderInfo;
  model: CustomProviderModelInfo;
}

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
