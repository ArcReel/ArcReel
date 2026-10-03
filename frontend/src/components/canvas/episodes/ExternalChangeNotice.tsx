import { useTranslation } from "react-i18next";
import { TriangleAlert } from "lucide-react";

import { API } from "@/api";
import { PrimaryButton } from "@/components/legacy/PrimaryButton";
import { SecondaryButton } from "@/components/legacy/SecondaryButton";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
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
      await useProjectsStore.getState().refreshProject(projectName);
      useAppStore.getState().pushToast(t("episodes_view_external_done", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(t("source_file_change_failed", { name, message: errMsg(err) }), "error");
    }
  };

  return (
    <div className="mt-4 space-y-3">
      {changes.map((item) => {
        const name = fileName(item.source_file);
        return (
          <section
            key={item.source_file}
            aria-label={t("episodes_view_external_title", { name })}
            className="rounded-md border px-4 py-3"
            style={{ borderColor: "var(--color-warm)", background: "oklch(0.24 0.03 60 / 0.35)" }}
          >
            <h2 className="flex items-center gap-2 text-[13px] font-semibold text-text">
              <TriangleAlert className="h-4 w-4 shrink-0 text-[var(--color-warm)]" aria-hidden />
              {t("episodes_view_external_title", { name })}
            </h2>
            {item.problem !== null ? (
              <p role="alert" className="mt-2 text-[12.5px] leading-[1.7] text-[var(--color-warm)]">
                {item.problem}
              </p>
            ) : (
              <>
                <p className="mt-2 text-[12px] leading-[1.7] text-text-3">{t("episodes_view_external_hint")}</p>
                <p className="mt-2 whitespace-pre-line text-[12.5px] leading-[1.7] text-text-2">
                  {item.impact?.text || t("episodes_view_external_none")}
                </p>
              </>
            )}
            <div className="mt-3 flex flex-wrap gap-2">
              {item.problem === null ? (
                <PrimaryButton size="sm" disabled={change.busy} onClick={() => void accept(item)}>
                  {t("episodes_view_external_accept")}
                </PrimaryButton>
              ) : null}
              <SecondaryButton size="sm" onClick={() => onLocate(item.source_file)}>
                {t("episodes_view_external_locate")}
              </SecondaryButton>
            </div>
          </section>
        );
      })}
      {change.dialog}
    </div>
  );
}
