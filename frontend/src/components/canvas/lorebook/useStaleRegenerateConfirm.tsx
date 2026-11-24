import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
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
  willBeStale?: boolean;
}

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
  hasSheet: boolean;
  onGenerate: () => void;
}): {
  request: () => void;
  confirm: (options?: StaleConfirmOptions) => Promise<boolean>;
  dialog: ReactNode;
} {
  const { t } = useTranslation(["assets", "common"]);
  const [state, setState] = useState<ImpactState>({ phase: "closed" });
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
      API.getAssetRegenerationImpact(projectName, assetType, name, derivativeName, { signal: controller.signal })
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
        </AlertDialogHeader>
        <AlertDialogBody tabIndex={0} role="region" aria-label={t("sheet_regenerate_stale_title")}>
          <AlertDialogDescription className="wrap-break-word">{description}</AlertDialogDescription>
        </AlertDialogBody>
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
