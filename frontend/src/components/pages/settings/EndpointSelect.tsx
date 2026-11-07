import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { DiscoveryFormat, EndpointDescriptor, EndpointKey, ImageCap, MediaType } from "@/types";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import { isComfyuiEndpoint, isComfyuiProtocol } from "./customProviderHelpers";

const MEDIA_GROUP_LABEL_KEY: Record<MediaType, string> = {
  text: "endpoint_text_group",
  image: "endpoint_image_group",
  video: "endpoint_video_group",
  audio: "endpoint_audio_group",
};

// Group order derives from declarations above; new media types require one new declaration only.
const MEDIA_ORDER = Object.keys(MEDIA_GROUP_LABEL_KEY) as MediaType[];

function imageCapabilityKey(caps: ImageCap[]): string {
  if (caps.length === 2) return "image_capability_both";
  return caps[0] === "text_to_image" ? "image_capability_t2i" : "image_capability_i2i";
}

interface EndpointSelectProps {
  /** Trigger button ID for the field label's `htmlFor` association. */
  id?: string;
  /** Accessible trigger names without visible labels, for example inline rows. */
  "aria-label"?: string;
  value: EndpointKey;
  onChange: (next: EndpointKey) => void;
  /**
   * Host provider discovery protocols. ComfyUI endpoints/providers exclusively pair with ComfyUI protocols
   * (bidirectional server validation, docs/adr/0081), so selectors list compatible halves only; otherwise users discover
   * incompatible associations only through save-time 422s.
   */
  protocol: DiscoveryFormat;
  disabled?: boolean;
  /** Narrow inline positions: triggers show endpoint names only, keeping request paths in dropdown options. */
  compact?: boolean;
}

/**
 * Custom-provider model endpoint selectors: group by media, showing names/methods/paths,
 * plus text-to-image/image-to-image flags for images. All options come from endpoint catalogs, the backend sole truth.
 */
export function EndpointSelect({
  id,
  "aria-label": ariaLabel,
  value,
  onChange,
  protocol,
  disabled,
  compact = false,
}: EndpointSelectProps) {
  const { t } = useTranslation("dashboard");
  const endpoints = useEndpointCatalogStore((s) => s.endpoints);
  const initialized = useEndpointCatalogStore((s) => s.initialized);
  const loading = useEndpointCatalogStore((s) => s.loading);
  const fetchCatalog = useEndpointCatalogStore((s) => s.fetch);
  const refreshCatalog = useEndpointCatalogStore((s) => s.refresh);

  // Fetch unready catalogs once; stores short-circuit, making repeated mounts safe. Still unready after fetch means failure, then show retries.
  const [attempted, setAttempted] = useState(false);
  useEffect(() => {
    if (!initialized) void fetchCatalog().finally(() => setAttempted(true));
  }, [initialized, fetchCatalog]);

  const groups = useMemo(() => {
    const wantsComfyui = isComfyuiProtocol(protocol);
    const usable = endpoints.filter((e) => isComfyuiEndpoint(e) === wantsComfyui);
    return MEDIA_ORDER.map((media) => ({ media, options: usable.filter((e) => e.media_type === media) })).filter(
      (group) => group.options.length > 0,
    );
  }, [endpoints, protocol]);

  const label = (endpoint: EndpointDescriptor) => endpoint.display_name ?? t(endpoint.display_name_key);
  const selected = endpoints.find((e) => e.key === value);

  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <Select<EndpointKey>
        // Empty values mean rows lack endpoints (hosts switched to ComfyUI without compatible endpoints); display placeholders.
        value={value === "" ? null : value}
        onValueChange={(next) => {
          // Reselecting identical values is unchanged; callers invalidate unsaved overrides based on actual endpoint changes.
          if (next !== null && next !== value) onChange(next);
        }}
        disabled={disabled || groups.length === 0}
      >
        <SelectTrigger id={id} aria-label={ariaLabel} className="w-full">
          <SelectValue>
            {() =>
              value === "" ? (
                <span className="truncate text-muted-foreground">
                  {t(initialized ? "cp_endpoint_unselected" : "endpoint_catalog_loading")}
                </span>
              ) : selected ? (
                <span className="flex min-w-0 items-baseline gap-2">
                  <span className="truncate">{label(selected)}</span>
                  {!compact && (
                    <span className="truncate font-mono text-xs text-muted-foreground">
                      {selected.request_path_template}
                    </span>
                  )}
                </span>
              ) : (
                // Saved endpoints missing from catalogs (drift/temporary backend removal) show original keys, never impersonate first options.
                <span className="truncate font-mono">{value}</span>
              )
            }
          </SelectValue>
        </SelectTrigger>
        <SelectContent alignItemWithTrigger={false}>
          {groups.map((group) => (
            <SelectGroup key={group.media}>
              <SelectLabel>{t(MEDIA_GROUP_LABEL_KEY[group.media])}</SelectLabel>
              {group.options.map((endpoint) => (
                <SelectItem key={endpoint.key} value={endpoint.key}>
                  <span className="flex min-w-0 flex-col gap-0.5">
                    <span className="truncate">{label(endpoint)}</span>
                    <span className="flex min-w-0 items-baseline gap-1.5 text-xs text-muted-foreground">
                      <span className="font-mono">{endpoint.request_method}</span>
                      <span className="truncate font-mono">{endpoint.request_path_template}</span>
                      {endpoint.image_capabilities && (
                        <span className="shrink-0">· {t(imageCapabilityKey(endpoint.image_capabilities))}</span>
                      )}
                    </span>
                  </span>
                </SelectItem>
              ))}
            </SelectGroup>
          ))}
        </SelectContent>
      </Select>
      {/* Failed catalogs leave selectors without options; show retries, with store.refresh short-circuiting in-flight requests. */}
      {attempted && !initialized && !loading && (
        <Button variant="link" size="sm" className="self-start" onClick={() => void refreshCatalog()}>
          {t("endpoint_catalog_retry")}
        </Button>
      )}
    </div>
  );
}
