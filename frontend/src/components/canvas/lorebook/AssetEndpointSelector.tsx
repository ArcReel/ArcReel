import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { ProjectData } from "@/types/project";
import { errMsg } from "@/utils/async";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { useAssetImageEndpoints } from "@/hooks/useAssetImageEndpoints";
import { ASSET_IMAGE_SLOT_KEY, type AssetImageEndpointType } from "@/utils/asset-image-endpoint";
import { Button } from "@/components/ui/button";
import { ProviderModelSelect } from "@/components/shared/ProviderModelSelect";

interface AssetEndpointSelectorProps {
  projectName: string;
  assetType: AssetImageEndpointType;
  useImageToImage: boolean;
  /** 覆盖值（"" = 跟随当前生效端点，本次生成不带 payload 覆盖）。 */
  value: string;
  onChange: (value: string) => void;
  busy: boolean;
}

/**
 * 工作室左栏的图像端点选择器：显示当前会执行的端点、可临时切换本次生成用的端点，
 * 并支持把某端点设为本类型在本项目的默认（写 ``default_image_backend_<type>`` 项目槽位）。
 */
export function AssetEndpointSelector({
  projectName,
  assetType,
  useImageToImage,
  value,
  onChange,
  busy,
}: AssetEndpointSelectorProps) {
  const { t } = useTranslation(["assets", "common"]);
  const { options, providerNames, modelNames, current, loading } = useAssetImageEndpoints(assetType, useImageToImage);
  const [saving, setSaving] = useState(false);

  const sourceLabel =
    current.source === "auto"
      ? t("studio.endpoint_source_auto")
      : current.source.startsWith("project")
        ? t("studio.endpoint_source_project")
        : t("studio.endpoint_source_global");
  const fallbackLabel =
    current.source.startsWith("project") ? t("studio.endpoint_follow_project") : t("studio.endpoint_follow_global");

  const defaultTarget = value || current.value;
  const canSetDefault = defaultTarget !== "";

  const handleSetDefault = useCallback(async () => {
    if (!canSetDefault || saving) return;
    setSaving(true);
    try {
      const patch = { [ASSET_IMAGE_SLOT_KEY[assetType]]: defaultTarget } as Partial<ProjectData>;
      await API.updateProject(projectName, patch);
      await refreshAfterWrite(projectName, t);
      useAppStore.getState().pushToast(t("studio.endpoint_set_default_toast"), "success");
      onChange("");
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setSaving(false);
    }
  }, [canSetDefault, saving, defaultTarget, assetType, projectName, t, onChange]);

  return (
    <div className="space-y-1.5">
      <span className="block text-xs font-medium uppercase tracking-wider text-muted-foreground">
        {t("studio.endpoint_label")}
      </span>
      <ProviderModelSelect
        value={value}
        options={options}
        providerNames={providerNames}
        modelNames={modelNames}
        onChange={onChange}
        allowDefault
        defaultLabel={t("studio.endpoint_follow_default")}
        fallbackValue={current.value}
        fallbackLabel={fallbackLabel}
        placeholder={t("studio.endpoint_placeholder")}
        aria-label={t("studio.endpoint_label")}
      />
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-xs text-muted-foreground">
          {t("studio.endpoint_current", { source: sourceLabel })}
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void handleSetDefault()}
          disabled={!canSetDefault || busy || saving || loading}
        >
          {t("studio.endpoint_set_default")}
        </Button>
      </div>
    </div>
  );
}
