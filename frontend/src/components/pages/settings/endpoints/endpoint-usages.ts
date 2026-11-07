import type { CustomProviderInfo, CustomProviderModelInfo } from "@/types";

/** A model row's endpoint usage: its custom provider and model. */
export interface EndpointUsage {
  provider: CustomProviderInfo;
  model: CustomProviderModelInfo;
}

/** Group custom-provider model rows by endpoint key; only custom-provider models can specify an endpoint. */
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
