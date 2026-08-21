import { useEffect, useState } from "react";
import { Landmark, Link2, Package, Unlink, User } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import type { Asset, AssetType } from "@/types/asset";
import { AssetPickerModal } from "./AssetPickerModal";

interface Props {
  projectName: string;
  resourceType: AssetType;
  resourceId: string;
  matchedAssetId?: string;
  linkedAssetId?: string;
  onReload?: () => void | Promise<unknown>;
  busy?: boolean;
}

const TYPE_ICON = { character: User, scene: Landmark, prop: Package };

export function ProjectAssetLinkControl({
  projectName,
  resourceType,
  resourceId,
  matchedAssetId,
  linkedAssetId,
  onReload,
  busy = false,
}: Props) {
  const { t } = useTranslation("assets");
  const assetId = linkedAssetId ?? matchedAssetId;
  const [resolvedAsset, setResolvedAsset] = useState<{ id: string; asset: Asset } | null>(null);
  const asset = resolvedAsset && resolvedAsset.id === assetId ? resolvedAsset.asset : null;
  const [picking, setPicking] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!assetId) return () => { cancelled = true; };
    void API.getAsset(assetId)
      .then((result) => {
        if (!cancelled && result.asset.type === resourceType) {
          setResolvedAsset({ id: assetId, asset: result.asset });
        }
      })
      .catch(() => {
        // 全局资产可能已被删除；仍保留解除链接入口，不阻断项目卡片。
      });
    return () => { cancelled = true; };
  }, [assetId, resourceType]);

  const link = async (targetId: string) => {
    setSubmitting(true);
    try {
      const result = await API.linkProjectAsset({
        project_name: projectName,
        resource_type: resourceType,
        resource_id: resourceId,
        asset_id: targetId,
      });
      setResolvedAsset({ id: targetId, asset: result.asset });
      setPicking(false);
      useAppStore.getState().pushToast(t("link_global_asset_success", { name: result.asset.name }), "success");
      await onReload?.();
    } catch (error) {
      useAppStore.getState().pushToast(errMsg(error), "error");
    } finally {
      setSubmitting(false);
    }
  };

  const handleUnlink = async () => {
    setSubmitting(true);
    try {
      await API.unlinkProjectAsset(projectName, resourceType, resourceId);
      setResolvedAsset(null);
      useAppStore.getState().pushToast(t("unlink_global_asset_success"), "success");
      await onReload?.();
    } catch (error) {
      useAppStore.getState().pushToast(errMsg(error), "error");
    } finally {
      setSubmitting(false);
    }
  };

  const disabled = busy || submitting;
  const Icon = TYPE_ICON[resourceType];
  const imageUrl = asset ? API.getGlobalAssetUrl(asset.image_path, asset.updated_at) : null;

  return (
    <>
      {asset ? (
        <div
          className="mb-4 flex items-center gap-2 rounded-lg px-2 py-1.5"
          style={{ border: "1px solid var(--color-hairline-soft)", background: "oklch(0.18 0.010 265 / 0.45)" }}
          data-testid="global-asset-link-card"
        >
          <div className="grid h-9 w-12 shrink-0 place-items-center overflow-hidden rounded bg-[oklch(0.16_0.010_265)]">
            {imageUrl ? (
              <img
                src={imageUrl}
                alt={asset.name}
                className="h-full w-full object-contain"
              />
            ) : (
              <Icon className="h-4 w-4 text-[var(--color-text-4)]" />
            )}
          </div>
          <span className="min-w-0 flex-1 truncate text-xs font-medium text-[var(--color-text-2)]">
            {asset.name}
          </span>
          {linkedAssetId ? (
            <button
              type="button"
              disabled={disabled}
              onClick={() => { void handleUnlink(); }}
              aria-label={t("unlink_global_asset")}
              title={t("unlink_global_asset")}
              className="focus-ring inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--color-text-3)] transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:opacity-40"
            >
              <Unlink className="h-3.5 w-3.5" />
            </button>
          ) : (
            <button
              type="button"
              disabled={disabled}
              onClick={() => { void link(asset.id); }}
              aria-label={t("link_global_asset")}
              title={t("link_global_asset")}
              className="focus-ring inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--color-text-3)] transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:opacity-40"
            >
              <Link2 className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      ) : (
        <button
          type="button"
          disabled={disabled}
          onClick={() => {
            if (linkedAssetId) void handleUnlink();
            else setPicking(true);
          }}
          className="focus-ring mb-4 inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-lg text-xs text-[var(--color-text-3)] transition-colors hover:bg-[oklch(1_0_0_/_0.04)] disabled:opacity-40"
          style={{ border: "1px dashed var(--color-hairline)" }}
          aria-label={t(linkedAssetId ? "unlink_global_asset" : "link_global_asset")}
        >
          {linkedAssetId ? <Unlink className="h-3.5 w-3.5" /> : <Link2 className="h-3.5 w-3.5" />}
          <span>{t(linkedAssetId ? "unlink_global_asset" : "link_global_asset")}</span>
        </button>
      )}

      {picking ? (
        <AssetPickerModal
          type={resourceType}
          existingNames={new Set()}
          mode="link"
          onClose={() => setPicking(false)}
          onImport={(ids) => { if (ids[0]) void link(ids[0]); }}
        />
      ) : null}
    </>
  );
}
