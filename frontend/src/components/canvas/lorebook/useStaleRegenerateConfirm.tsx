import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { errMsg } from "@/utils/async";
import type { AssetRegenerationImpact, AssetSheetStatusRow, AssetSheetType } from "@/types";

type ImpactState =
  | { phase: "closed" }
  | { phase: "loading" }
  | { phase: "ready"; impact: AssetRegenerationImpact }
  | { phase: "failed"; message: string };

/**
 * 单张重生一张过期资产图前的确认：列出会随之过期的分镜图、视频与衍生图数量。
 * 非过期的资产图直接生成，不经确认。
 */
export function useStaleRegenerateConfirm({
  projectName,
  assetType,
  name,
  derivativeName,
  status,
  onGenerate,
}: {
  projectName: string;
  assetType: AssetSheetType;
  name: string;
  derivativeName?: string;
  status: AssetSheetStatusRow | undefined;
  onGenerate: () => void;
}): { request: () => void; dialog: ReactNode } {
  const { t } = useTranslation("assets");
  const [state, setState] = useState<ImpactState>({ phase: "closed" });

  const request = () => {
    if (status?.status !== "stale") {
      onGenerate();
      return;
    }
    setState({ phase: "loading" });
    (derivativeName
      ? API.getAssetRegenerationImpact(projectName, assetType, name, derivativeName)
      : API.getAssetRegenerationImpact(projectName, assetType, name))
      .then((impact) => setState({ phase: "ready", impact }))
      .catch((err: unknown) => setState({ phase: "failed", message: errMsg(err) }));
  };

  const close = () => setState({ phase: "closed" });

  let description: ReactNode = null;
  if (state.phase === "ready") {
    description = (
      <>
        <p>
          {t("sheet_regenerate_stale_impact", {
            storyboards: state.impact.storyboards,
            videos: state.impact.videos,
          })}
        </p>
        {state.impact.derivatives > 0 && (
          <p>{t("sheet_regenerate_stale_derivatives", { count: state.impact.derivatives })}</p>
        )}
      </>
    );
  } else if (state.phase === "failed") {
    description = t("sheet_regenerate_impact_failed", { message: state.message });
  }

  const dialog = (
    <ConfirmDialog
      open={state.phase !== "closed"}
      title={t("sheet_regenerate_stale_title")}
      description={description}
      confirmLabel={t("sheet_regenerate_confirm")}
      loading={state.phase === "loading"}
      onConfirm={() => {
        close();
        onGenerate();
      }}
      onCancel={close}
    />
  );

  return { request, dialog };
}
