import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { errMsg } from "@/utils/async";
import type { AssetRegenerationImpact, AssetSheetStatusRow, AssetSheetType } from "@/types";

type ImpactState =
  | { phase: "closed" }
  | { phase: "checking" }
  | { phase: "loading" }
  | { phase: "ready"; impact: AssetRegenerationImpact }
  | { phase: "failed"; message: string };

/**
 * 单张重生一张过期资产图前的确认：列出会随之过期的分镜图、视频与衍生图数量。
 * 是否过期以服务端此刻的判定为准：本地状态行可能还没随刚改的描述刷新，或尚未取到。
 * 待生成的资产图直接生成；其余先问服务端，非过期直接生成，过期才确认。
 */
export function useStaleRegenerateConfirm({
  projectName,
  assetType,
  name,
  derivativeName,
  status,
  hasSheet,
  onGenerate,
}: {
  projectName: string;
  assetType: AssetSheetType;
  name: string;
  derivativeName?: string;
  status: AssetSheetStatusRow | undefined;
  /** 资产条目上是否登记了资产图文件；没有时就是首次生成。 */
  hasSheet: boolean;
  onGenerate: () => void;
}): { request: () => void; dialog: ReactNode } {
  const { t } = useTranslation(["assets", "common"]);
  const [state, setState] = useState<ImpactState>({ phase: "closed" });

  const request = () => {
    if (!hasSheet || status?.status === "missing") {
      onGenerate();
      return;
    }
    setState({ phase: status?.status === "stale" ? "loading" : "checking" });
    (derivativeName
      ? API.getAssetRegenerationImpact(projectName, assetType, name, derivativeName)
      : API.getAssetRegenerationImpact(projectName, assetType, name))
      .then((impact) => {
        if (impact.stale) {
          setState({ phase: "ready", impact });
          return;
        }
        setState({ phase: "closed" });
        onGenerate();
      })
      .catch((err: unknown) => setState({ phase: "failed", message: errMsg(err) }));
  };

  const close = () => setState({ phase: "closed" });

  let description: ReactNode = null;
  if (state.phase === "ready") {
    description = (
      <>
        {t("sheet_regenerate_stale_impact", {
          storyboards: state.impact.storyboards,
          videos: state.impact.videos,
        })}
        {state.impact.derivatives > 0 && <> {t("sheet_regenerate_stale_derivatives", { count: state.impact.derivatives })}</>}
      </>
    );
  } else if (state.phase === "failed") {
    description = t("sheet_regenerate_impact_failed", { message: state.message });
  } else if (state.phase === "loading") {
    description = t("common:loading");
  }

  const dialog = (
    <AlertDialog
      open={state.phase !== "closed" && state.phase !== "checking"}
      onOpenChange={(next) => {
        if (!next) close();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("sheet_regenerate_stale_title")}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction
            disabled={state.phase === "loading"}
            onClick={() => {
              close();
              onGenerate();
            }}
          >
            {t("sheet_regenerate_confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );

  return { request, dialog };
}
