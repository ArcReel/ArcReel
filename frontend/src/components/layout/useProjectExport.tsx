import { useCallback, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { ArchiveDiagnosticsDialog } from "@/components/shared/ArchiveDiagnosticsDialog";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { triggerBrowserDownload } from "@/utils/download";
import type { ExportDiagnostics } from "@/types";
import { ExportScopeDialog, type ExportScope } from "./ExportScopeDialog";

interface ProjectExportOptions {
  /** Episode targeted by dialog links to Open episode editing; omitting it shows only the explanation that finished videos export from editing views. */
  editViewEpisode?: { episode: number; name: string } | null;
  onOpenEditView?: (episode: number) => void;
}

interface ProjectExport {
  /** Open export-scope dialogs, then export `projectName` after selection. */
  open: (projectName: string) => void;
  /** Close dialogs awaiting scope selection, e.g. when switching projects or entering demos. */
  close: () => void;
  /** Project currently requesting export; reset once downloads start. */
  exporting: string | null;
  /** Export-scope and diagnostic dialogs, rendered in caller component trees. */
  element: ReactNode;
}

/**
 * Project ZIP exports shared by workspace headers/lobby card menus. Scope selection starts browser downloads, silently on successful start;
 * exports carrying diagnostics show counts/list diagnostics; failures show causes and enter workspace notifications.
 */
export function useProjectExport({ editViewEpisode, onOpenEditView }: ProjectExportOptions = {}): ProjectExport {
  const { t } = useTranslation("dashboard");
  const [target, setTarget] = useState<string | null>(null);
  const [exporting, setExporting] = useState<string | null>(null);
  const [diagnostics, setDiagnostics] = useState<ExportDiagnostics | null>(null);

  const close = useCallback(() => setTarget(null), []);

  const exportProject = async (projectName: string, scope: ExportScope) => {
    setTarget(null);
    setExporting(projectName);
    try {
      const { download_token, diagnostics: found } = await API.requestExportToken(projectName, scope);
      triggerBrowserDownload(API.getExportDownloadUrl(projectName, download_token, scope));
      const count = found.blocking.length + found.auto_fixed.length + found.warnings.length;
      if (count > 0) {
        setDiagnostics(found);
        useAppStore.getState().pushToast(t("project_zip_download_started_with_diagnostics", { count }), "warning");
      }
    } catch (err) {
      useAppStore.getState().pushNotification(t("export_failed", { message: errMsg(err) }), "error");
    } finally {
      setExporting(null);
    }
  };

  const element = (
    <>
      <ExportScopeDialog
        open={target !== null}
        onClose={() => setTarget(null)}
        onSelect={(scope) => {
          if (target) void exportProject(target, scope);
        }}
        editViewEpisode={editViewEpisode}
        onOpenEditView={
          onOpenEditView &&
          ((episode) => {
            setTarget(null);
            onOpenEditView(episode);
          })
        }
      />
      {diagnostics && (
        <ArchiveDiagnosticsDialog
          title={t("export_diagnostics_title")}
          description={t("export_diagnostics_description")}
          sections={[
            { key: "blocking", title: t("diagnostics_blocking"), severity: "blocking", items: diagnostics.blocking },
            { key: "auto_fixed", title: t("diagnostics_auto_fixed"), severity: "auto_fixed", items: diagnostics.auto_fixed },
            { key: "warnings", title: t("diagnostics_warnings"), severity: "warnings", items: diagnostics.warnings },
          ]}
          onClose={() => setDiagnostics(null)}
        />
      )}
    </>
  );

  return {
    open: setTarget,
    close,
    exporting,
    element,
  };
}
