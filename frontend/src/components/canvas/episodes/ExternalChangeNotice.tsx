import { useTranslation } from "react-i18next";
import { TriangleAlert } from "lucide-react";

import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { useAppStore } from "@/stores/app-store";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import type { ExternalSourceChange } from "@/types/episodes-view";
import { errMsg } from "@/utils/async";

import { useSourceFileChange } from "./useSourceFileChange";

interface ExternalChangeNoticeProps {
  projectName: string;
  changes: ExternalSourceChange[];
  onLocate: (sourceFile: string) => void;
}

function fileName(sourceFile: string): string {
  return sourceFile.slice(sourceFile.lastIndexOf("/") + 1);
}

/**
 * 「分集」视图顶部的提示：整本源文里在 ArcReel 之外被改动过的文件，各列出按快照对齐算出的受影响集。
 *
 * 「更新分集账本」带着提示里这份清单的 `revision` 提交，即确认这份清单；清单在此期间变了时换成新清单再确认一次。
 * 更新后提示随之消失，不另行提示。
 */
export function ExternalChangeNotice({ projectName, changes, onLocate }: ExternalChangeNoticeProps) {
  const { t } = useTranslation("dashboard");
  const change = useSourceFileChange();

  if (changes.length === 0) return null;

  const accept = async (item: ExternalSourceChange) => {
    const name = fileName(item.source_file);
    try {
      const reply = await change.run(
        t("episodes_view_external_accept_title", { name }),
        t("episodes_view_external_accept"),
        (revision) => API.acceptExternalSourceChange(projectName, name, revision ?? item.revision),
      );
      if (reply === null) return;
      await refreshAfterWrite(projectName, t);
    } catch (err) {
      useAppStore.getState().pushToast(t("source_file_change_failed", { name, message: errMsg(err) }), "error");
    }
  };

  return (
    <div className="flex flex-col gap-3 pt-4">
      {changes.map((item) => {
        const name = fileName(item.source_file);
        return (
          <section
            key={item.source_file}
            aria-label={t("episodes_view_external_title", { name })}
            className="flex flex-col gap-2 rounded-lg border border-warn/50 bg-warn/10 px-4 py-3 text-sm"
          >
            <h2 className="flex items-center gap-2 font-medium text-foreground">
              <TriangleAlert className="size-4 shrink-0 text-warn" aria-hidden />
              {t("episodes_view_external_title", { name })}
            </h2>
            {item.problem !== null ? (
              <p role="alert" className="text-warn">
                {item.problem}
              </p>
            ) : (
              <>
                <p className="text-muted-foreground">{t("episodes_view_external_hint")}</p>
                <p className="whitespace-pre-line text-subtle-foreground">
                  {item.impact?.text || t("episodes_view_external_none")}
                </p>
              </>
            )}
            <div className="flex flex-wrap gap-2 pt-1">
              {item.problem === null ? (
                <Button size="sm" disabled={change.busy} onClick={() => void accept(item)}>
                  {t("episodes_view_external_accept")}
                </Button>
              ) : null}
              <Button size="sm" variant="outline" onClick={() => onLocate(item.source_file)}>
                {t("episodes_view_external_locate")}
              </Button>
            </div>
          </section>
        );
      })}
      {change.dialog}
    </div>
  );
}
