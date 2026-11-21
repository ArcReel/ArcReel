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

const MEDIA_ORDER = Object.keys(MEDIA_GROUP_LABEL_KEY) as MediaType[];

function imageCapabilityKey(caps: ImageCap[]): string {
  if (caps.length === 2) return "image_capability_both";
  return caps[0] === "text_to_image" ? "image_capability_t2i" : "image_capability_i2i";
}

interface EndpointSelectProps {
  id?: string;
  "aria-label"?: string;
  value: EndpointKey;
  onChange: (next: EndpointKey) => void;
  /**
   * 宿主供应商的模型发现协议。ComfyUI 端点与 ComfyUI 协议供应商互为对方的唯一对手方
   * （服务端双向校验，见 docs/adr/0081），选择器据此只列得上的那一半——否则用户要撞上
   * 保存时的 422 才知道这条挂不上去。
   */
  protocol: DiscoveryFormat;
  disabled?: boolean;
  compact?: boolean;
}

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

  // 目录未就绪时取一次；store 自身有短路，重复挂载安全。取过一次仍未就绪即取失败，才给重试入口
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
        value={value === "" ? null : value}
        onValueChange={(next) => {
          // 同值重选不算变化：调用方按端点是否变化决定是否作废未保存的能力覆盖
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
                // 已存的端点不在当前目录里（数据漂移或后端临时移除）：显示原始 key，不冒充第一项
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
      {attempted && !initialized && !loading && (
        <Button variant="link" size="sm" className="self-start" onClick={() => void refreshCatalog()}>
          {t("endpoint_catalog_retry")}
        </Button>
      )}
    </div>
  );
}
