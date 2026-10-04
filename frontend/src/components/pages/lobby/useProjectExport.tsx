import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { ExportScopeDialog, type ExportScope } from "@/components/layout/ExportScopeDialog";
import { ArchiveDiagnosticsDialog } from "@/components/shared/ArchiveDiagnosticsDialog";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { triggerBrowserDownload } from "@/utils/download";
import type { ExportDiagnostics, ProjectSummary } from "@/types";

/**
 * 卡片菜单里的「导出」：复用工作区的导出范围对话框，选定范围后由浏览器下载项目 ZIP。导出带诊断时提示并列出诊断，
 * 失败时提示原因。大厅没有当前集，对话框里只显示成片在剪辑视图导出的说明，不带打开剪辑视图的链接。
 */
export function useProjectExport(): { startExport: (project: ProjectSummary) => void; element: ReactNode } {
  const { t } = useTranslation("dashboard");
  const [target, setTarget] = useState<ProjectSummary | null>(null);
  const [diagnostics, setDiagnostics] = useState<ExportDiagnostics | null>(null);

  const startExport = (project: ProjectSummary) => setTarget(project);

  const exportProject = async (project: ProjectSummary, scope: ExportScope) => {
    setTarget(null);
    try {
      const { download_token, diagnostics: found } = await API.requestExportToken(project.name, scope);
      triggerBrowserDownload(API.getExportDownloadUrl(project.name, download_token, scope));
      const count = found.blocking.length + found.auto_fixed.length + found.warnings.length;
      if (count > 0) {
        setDiagnostics(found);
        useAppStore
          .getState()
          .pushToast(t("project_zip_download_started_with_diagnostics", { count }), "warning");
      }
    } catch (err) {
      useAppStore.getState().pushToast(t("export_failed", { message: errMsg(err) }), "error");
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

  return { startExport, element };
}
