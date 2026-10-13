import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Info, Loader2, X } from "lucide-react";

import { API } from "@/api";
import { MigrationReportContent, migrationReportItemCount } from "@/components/shared/MigrationReportContent";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAppStore } from "@/stores/app-store";
import type { WorkflowMigrationReport } from "@/types/workflow";
import { errMsg } from "@/utils/async";
import type { EpisodeLedger } from "@/utils/episode-display";

/**
 * 升级后的一次性提示：「本次升级调整了 N 项内容」，点开是只读报告，关闭后记到服务端、不再出现。
 * 报告由制作状态给出，只在非空且尚未确认过时出现。
 */
export function MigrationReportNotice({
  projectName,
  report,
  episodes,
}: {
  projectName: string;
  report: WorkflowMigrationReport;
  episodes: EpisodeLedger;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [open, setOpen] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  // 只隐藏确认过的那一份：之后再有一次升级，新报告照常出现。
  const [dismissedAt, setDismissedAt] = useState<string | null>(null);

  if (dismissedAt === report.migrated_at) return null;
  const count = migrationReportItemCount(report);
  if (count === 0) return null;
  const label = t("dashboard:migration_report_notice", { count });

  const dismiss = async () => {
    if (dismissing) return;
    setDismissing(true);
    try {
      await API.acknowledgeMigrationReport(projectName);
      setOpen(false);
      setDismissedAt(report.migrated_at);
    } catch (err) {
      useAppStore.getState().pushToast(t("dashboard:migration_report_dismiss_failed", { message: errMsg(err) }), "error");
    } finally {
      setDismissing(false);
    }
  };

  return (
    <div className="inline-flex h-7 items-center overflow-hidden rounded-full border border-border bg-card">
      <button
        type="button"
        className="focus-ring inline-flex h-full items-center gap-1.5 px-3 text-xs whitespace-nowrap transition-colors duration-fast hover:bg-foreground/5"
        aria-label={label}
        onClick={() => setOpen(true)}
      >
        <Info aria-hidden className="size-3.5 text-primary" />
        <span aria-hidden className="hidden text-foreground @3xl/header:inline">
          {label}
        </span>
      </button>
      <span aria-hidden className="h-3.5 w-px bg-border" />
      <Button
        variant="ghost"
        size="icon-xs"
        className="mx-0.5"
        aria-label={t("dashboard:migration_report_dismiss")}
        disabled={dismissing}
        onClick={() => void dismiss()}
      >
        {dismissing ? <Loader2 aria-hidden className="animate-spin" /> : <X aria-hidden />}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent size="lg" showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>{t("dashboard:migration_report_title")}</DialogTitle>
            <DialogDescription>{t("dashboard:migration_report_description")}</DialogDescription>
          </DialogHeader>
          <DialogBody tabIndex={0} role="region" aria-label={t("dashboard:migration_report_title")}>
            <MigrationReportContent report={report} episodes={episodes} />
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" disabled={dismissing} onClick={() => void dismiss()}>
              {dismissing && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
              {t("dashboard:migration_report_acknowledge")}
            </Button>
            <DialogClose render={<Button />}>{t("common:close")}</DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
