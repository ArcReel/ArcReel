import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
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

export interface StaleConfirmOptions {
  /**
   * 保存未保存修改后资产图会过期（如改了描述）：服务端此刻还判为最新，也要确认。
   * 「保存并生成」在保存之前确认，取消时什么都不保存。
   */
  willBeStale?: boolean;
}

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
}): {
  /** 确认后生成。 */
  request: () => void;
  /** 只确认不生成：resolve 为是否继续。 */
  confirm: (options?: StaleConfirmOptions) => Promise<boolean>;
  dialog: ReactNode;
} {
  const { t } = useTranslation(["assets", "common"]);
  const [state, setState] = useState<ImpactState>({ phase: "closed" });
  // 等待创作者答复的那次确认；新一次确认或卸载时按取消结算上一次
  const pendingRef = useRef<{ resolve: (ok: boolean) => void; controller: AbortController } | null>(null);

  const settle = useCallback((ok: boolean) => {
    const pending = pendingRef.current;
    pendingRef.current = null;
    pending?.controller.abort();
    setState({ phase: "closed" });
    pending?.resolve(ok);
  }, []);

  useEffect(
    () => () => {
      const pending = pendingRef.current;
      pendingRef.current = null;
      pending?.controller.abort();
      pending?.resolve(false);
    },
    [],
  );

  const firstGeneration = !hasSheet || status?.status === "missing";

  const confirm = (options?: StaleConfirmOptions): Promise<boolean> => {
    if (firstGeneration) return Promise.resolve(true);
    const previous = pendingRef.current;
    pendingRef.current = null;
    previous?.controller.abort();
    previous?.resolve(false);

    const controller = new AbortController();
    const willBeStale = options?.willBeStale ?? false;
    return new Promise<boolean>((resolve) => {
      pendingRef.current = { resolve, controller };
      setState({ phase: willBeStale || status?.status === "stale" ? "loading" : "checking" });
      (derivativeName
        ? API.getAssetRegenerationImpact(projectName, assetType, name, derivativeName)
        : API.getAssetRegenerationImpact(projectName, assetType, name))
        .then((impact) => {
          if (controller.signal.aborted) return;
          if (impact.stale || willBeStale) {
            setState({ phase: "ready", impact });
            return;
          }
          settle(true);
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          setState({ phase: "failed", message: errMsg(err) });
        });
    });
  };

  const request = () => {
    if (firstGeneration) {
      onGenerate();
      return;
    }
    void confirm().then((ok) => {
      if (ok) onGenerate();
    });
  };

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
        if (!next) settle(false);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("sheet_regenerate_stale_title")}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction disabled={state.phase === "loading"} onClick={() => settle(true)}>
            {t("sheet_regenerate_confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );

  return { request, confirm, dialog };
}
