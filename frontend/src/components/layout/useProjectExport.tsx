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
  /** 对话框提示里「打开『集名』的剪辑视图」链接指向的集；不传时只显示成片在剪辑视图导出的说明。 */
  editViewEpisode?: { episode: number; name: string } | null;
  onOpenEditView?: (episode: number) => void;
}

interface ProjectExport {
  /** 打开导出范围对话框，选定范围后导出 `projectName`。 */
  open: (projectName: string) => void;
  /** 关闭尚未选定范围的对话框，如切到别的项目或演示项目时。 */
  close: () => void;
  /** 正在为哪个项目请求导出；下载开始后复位。 */
  exporting: string | null;
  /** 导出范围对话框与诊断对话框，渲染在调用方组件树里。 */
  element: ReactNode;
}

/**
 * 导出项目 ZIP：工作区顶栏与大厅卡片菜单共用。选定范围后由浏览器下载；顺利开始时不提示，
 * 导出包带诊断时提示条数并列出诊断，失败时提示原因并记入工作区通知。
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
