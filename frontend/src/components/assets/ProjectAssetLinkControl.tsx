import { useEffect, useState } from "react";
import { Link, Unlink } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { Asset, AssetType } from "@/types/asset";
import { errMsg } from "@/utils/async";
import { AssetPickerModal } from "./AssetPickerModal";

interface Props {
  projectName: string;
  resourceType: AssetType;
  resourceId: string;
  matchedAssetId?: string;
  linkedAssetId?: string;
  imageUsage?: "main" | "reference";
  voiceSource?: "reference_audio" | "voice_id" | "none";
  showImageUsageControl?: boolean;
  showVoiceSourceControl?: boolean;
  onAssetResolved?: (asset: Asset | null) => void;
  onReload?: () => void | Promise<unknown>;
  busy?: boolean;
}

export function ProjectAssetLinkControl({
  projectName, resourceType, resourceId, matchedAssetId, linkedAssetId,
  imageUsage = "main", voiceSource = "none", showImageUsageControl = true,
  showVoiceSourceControl = true, onAssetResolved, onReload, busy = false,
}: Props) {
  const { t } = useTranslation("assets");
  const assetId = linkedAssetId ?? matchedAssetId;
  const [resolvedAsset, setResolvedAsset] = useState<{ id: string; asset: Asset } | null>(null);
  const asset = resolvedAsset && resolvedAsset.id === assetId ? resolvedAsset.asset : null;
  const [picking, setPicking] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!assetId) {
      onAssetResolved?.(null);
      return () => { cancelled = true; };
    }
    void API.getAsset(assetId).then(({ asset: value }) => {
      if (!cancelled && value.type === resourceType) {
        setResolvedAsset({ id: assetId, asset: value });
        onAssetResolved?.(value);
      }
    }).catch(() => {
      if (!cancelled) {
        onAssetResolved?.(null);
      }
    });
    return () => { cancelled = true; };
  }, [assetId, resourceType, onAssetResolved]);

  const run = async (operation: () => Promise<unknown>) => {
    setSubmitting(true);
    try {
      await operation();
      await onReload?.();
    } catch (error) {
      useAppStore.getState().pushToast(errMsg(error), "error");
    } finally {
      setSubmitting(false);
    }
  };

  const link = (targetId: string) => run(async () => {
    const result = await API.linkProjectAsset({ project_name: projectName, resource_type: resourceType, resource_id: resourceId, asset_id: targetId });
    setResolvedAsset({ id: targetId, asset: result.asset });
    onAssetResolved?.(result.asset);
    setPicking(false);
    useAppStore.getState().pushToast(t("link_global_asset_success", { name: result.asset.name }), "success");
  });
  const unlink = () => run(async () => {
    await API.unlinkProjectAsset(projectName, resourceType, resourceId);
    setResolvedAsset(null);
    onAssetResolved?.(null);
    useAppStore.getState().pushToast(t("unlink_global_asset_success"), "success");
  });
  const configure = (patch: { image_usage?: "main" | "reference"; voice_source?: "reference_audio" | "voice_id" | "none" }) =>
    run(() => API.configureProjectAssetLink({ project_name: projectName, resource_type: resourceType, resource_id: resourceId, ...patch }));
  const disabled = busy || submitting;

  return (
    <>
      <div className="flex items-center gap-1.5" data-testid="global-asset-link-control">
        {assetId ? <>
          {showImageUsageControl ? (
            <button type="button" disabled={disabled} onClick={() => { void configure({ image_usage: imageUsage === "main" ? "reference" : "main" }); }} className="focus-ring rounded px-1.5 py-1 text-[10px] text-[var(--color-text-3)] hover:bg-[oklch(1_0_0_/_0.05)] disabled:opacity-40" title={asset?.name}>
              {imageUsage === "main" ? t("global_asset_as_main") : t("global_asset_as_reference")}
            </button>
          ) : null}
          {showVoiceSourceControl && resourceType === "character" && asset && (asset.audio_path || asset.voice_id) ? (
            <select aria-label={t("global_asset_voice_source")} value={voiceSource} disabled={disabled} onChange={(event) => { void configure({ voice_source: event.target.value as "reference_audio" | "voice_id" | "none" }); }} className="h-7 rounded border border-[var(--color-hairline)] bg-transparent px-1 text-[10px] text-[var(--color-text-3)]">
              {asset.audio_path ? <option value="reference_audio">{t("global_asset_reference_audio")}</option> : null}
              {asset.voice_id ? <option value="voice_id">Voice ID</option> : null}
              <option value="none">{t("global_asset_no_voice")}</option>
            </select>
          ) : null}
          <button type="button" disabled={disabled} onClick={() => { void unlink(); }} aria-label={t("unlink_global_asset")} title={t("unlink_global_asset")} className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-3)] transition-colors hover:bg-[oklch(1_0_0_/_0.05)] hover:text-[var(--color-text)] disabled:opacity-40">
            <Unlink className="h-3.5 w-3.5" />
          </button>
        </> : (
          <button type="button" disabled={disabled} onClick={() => setPicking(true)} aria-label={t("link_global_asset")} title={t("link_global_asset")} className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-3)] transition-colors hover:bg-[oklch(1_0_0_/_0.05)] hover:text-[var(--color-text)] disabled:opacity-40">
            <Link className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      {picking ? <AssetPickerModal type={resourceType} existingNames={new Set()} mode="link" onClose={() => setPicking(false)} onImport={(ids) => { if (ids[0]) void link(ids[0]); }} /> : null}
    </>
  );
}

interface ProjectAssetConfigurationControlProps {
  projectName: string;
  resourceType: AssetType;
  resourceId: string;
  onReload?: () => void | Promise<unknown>;
  busy?: boolean;
}

function useProjectAssetConfiguration({
  projectName,
  resourceType,
  resourceId,
  onReload,
  busy = false,
}: ProjectAssetConfigurationControlProps) {
  const [submitting, setSubmitting] = useState(false);

  const configure = async (patch: {
    image_usage?: "main" | "reference";
    voice_source?: "reference_audio" | "voice_id" | "none";
  }) => {
    setSubmitting(true);
    try {
      await API.configureProjectAssetLink({
        project_name: projectName,
        resource_type: resourceType,
        resource_id: resourceId,
        ...patch,
      });
      await onReload?.();
    } catch (error) {
      useAppStore.getState().pushToast(errMsg(error), "error");
    } finally {
      setSubmitting(false);
    }
  };

  return { configure, disabled: busy || submitting };
}

interface ProjectAssetVoiceSourceSwitchProps extends ProjectAssetConfigurationControlProps {
  asset: Asset | null;
  voiceSource?: "reference_audio" | "voice_id" | "none";
}

/** 只在全局角色确实存在另一个可用音源时提供切换；历史 none 状态可重新启用首个音源。 */
export function ProjectAssetVoiceSourceSwitch({
  asset,
  voiceSource = "none",
  ...props
}: ProjectAssetVoiceSourceSwitchProps) {
  const { t } = useTranslation("assets");
  const { configure, disabled } = useProjectAssetConfiguration(props);

  const nextSource = voiceSource === "reference_audio"
    ? (asset?.voice_id ? "voice_id" : null)
    : voiceSource === "voice_id"
      ? (asset?.audio_path ? "reference_audio" : null)
      : asset?.audio_path
        ? "reference_audio"
        : asset?.voice_id
          ? "voice_id"
          : null;

  if (!nextSource) return null;

  const label = nextSource === "reference_audio"
    ? t("switch_to_reference_audio")
    : t("switch_to_voice_id");

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => { void configure({ voice_source: nextSource }); }}
      className="focus-ring rounded-md px-2 py-1 text-[10px] font-medium text-[var(--color-text-3)] transition-colors hover:bg-[oklch(1_0_0_/_0.05)] hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-40"
    >
      {label}
    </button>
  );
}
