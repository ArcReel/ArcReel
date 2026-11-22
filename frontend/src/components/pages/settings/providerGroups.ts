import type { ProviderInfo } from "@/types";

export interface ProviderVendorChannelSpec {
  providerId: string;
  tabLabelKey: string;
}

export interface ProviderVendorGroupSpec {
  id: string;
  labelKey: string;
  iconProviderId: string;
  channels: readonly ProviderVendorChannelSpec[];
}

export interface ProviderChannelOption {
  providerId: string;
  tabLabelKey: string;
  credentialCount: number;
}

interface PresetRailEntryBase {
  railItemId: string;
  iconProviderId: string;
  credentialCount: number;
  targetProviderId: string;
}

export type PresetRailEntry =
  | (PresetRailEntryBase & { labelKey: string; displayName?: never })
  | (PresetRailEntryBase & { displayName: string; labelKey?: never });

export const PROVIDER_VENDOR_GROUPS: readonly ProviderVendorGroupSpec[] = [
  {
    id: "gemini",
    labelKey: "provider_vendor_gemini",
    iconProviderId: "gemini-aistudio",
    channels: [
      { providerId: "gemini-aistudio", tabLabelKey: "provider_channel_gemini_aistudio" },
      { providerId: "gemini-vertex", tabLabelKey: "provider_channel_gemini_vertex" },
    ],
  },
  {
    id: "ark",
    labelKey: "provider_vendor_ark",
    iconProviderId: "ark",
    channels: [
      { providerId: "ark", tabLabelKey: "provider_channel_ark_payg" },
      { providerId: "ark-agent-plan", tabLabelKey: "provider_channel_ark_agent_plan" },
    ],
  },
];

const GROUP_BY_PROVIDER_ID = new Map<string, ProviderVendorGroupSpec>(
  PROVIDER_VENDOR_GROUPS.flatMap((group) => group.channels.map((channel) => [channel.providerId, group] as const)),
);

function presetRailId(providerId: string): string {
  return `preset:${providerId}`;
}

function vendorRailId(groupId: string): string {
  return `preset-vendor:${groupId}`;
}

function toProviderMap(providers: readonly ProviderInfo[]): Map<string, ProviderInfo> {
  return new Map(providers.map((provider) => [provider.id, provider]));
}

function resolveActiveGroup(
  providerId: string,
  providerMap: ReadonlyMap<string, ProviderInfo>,
): { group: ProviderVendorGroupSpec; channels: ProviderChannelOption[] } | null {
  const group = GROUP_BY_PROVIDER_ID.get(providerId);
  if (!group) return null;
  const channels = group.channels.flatMap((channel) => {
    const info = providerMap.get(channel.providerId);
    if (!info) return [];
    return [
      {
        providerId: channel.providerId,
        tabLabelKey: channel.tabLabelKey,
        credentialCount: info.credential_count,
      },
    ];
  });
  return channels.length >= 2 ? { group, channels } : null;
}

export function buildPresetRailEntries(
  providers: readonly ProviderInfo[],
  activeProviderId: string | null,
): PresetRailEntry[] {
  const providerMap = toProviderMap(providers);
  const emittedGroups = new Set<string>();
  const entries: PresetRailEntry[] = [];

  for (const provider of providers) {
    const activeGroup = resolveActiveGroup(provider.id, providerMap);
    if (activeGroup) {
      const { group, channels } = activeGroup;
      if (emittedGroups.has(group.id)) continue;
      emittedGroups.add(group.id);
      const stayOnCurrent = activeProviderId !== null && channels.some((ch) => ch.providerId === activeProviderId);
      entries.push({
        railItemId: vendorRailId(group.id),
        iconProviderId: group.iconProviderId,
        labelKey: group.labelKey,
        credentialCount: channels.reduce((sum, ch) => sum + ch.credentialCount, 0),
        targetProviderId: stayOnCurrent ? activeProviderId : channels[0].providerId,
      });
      continue;
    }

    entries.push({
      railItemId: presetRailId(provider.id),
      iconProviderId: provider.id,
      displayName: provider.display_name,
      credentialCount: provider.credential_count,
      targetProviderId: provider.id,
    });
  }

  return entries;
}

export function resolvePresetRailActiveId(providers: readonly ProviderInfo[], providerId: string): string {
  const activeGroup = resolveActiveGroup(providerId, toProviderMap(providers));
  return activeGroup ? vendorRailId(activeGroup.group.id) : presetRailId(providerId);
}

export function resolveVendorChannels(
  providers: readonly ProviderInfo[],
  providerId: string,
): ProviderChannelOption[] | undefined {
  return resolveActiveGroup(providerId, toProviderMap(providers))?.channels;
}
