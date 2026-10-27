import { useEffect, useRef, useState, type ChangeEvent, type ReactNode } from "react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { ArchiveDiagnosticsDialog } from "@/components/shared/ArchiveDiagnosticsDialog";
import { useAppStore } from "@/stores/app-store";
import { getProjectDisplayName } from "@/utils/project-display";
import type { ImportConflictPolicy, ImportFailureDiagnostics } from "@/types";
import type { WorkflowMigrationReport } from "@/types/workflow";
import { ImportConflictDialog } from "./ImportConflictDialog";
import { ImportReportConfirmDialog, type PendingImportReport } from "./ImportReportConfirmDialog";

type ImportDiagnosticsState =
  | { source: "success"; diagnostics: ImportFailureDiagnostics; navigateTo: string }
  | { source: "failure"; diagnostics: ImportFailureDiagnostics };

type ImportError = Error & {
  status?: number;
  conflict_project_name?: string;
  confirmation_required?: boolean;
  migration_report?: WorkflowMigrationReport;
  migration_report_episodes?: { episode: number; title: string }[];
  diagnostics?: ImportFailureDiagnostics;
};

/** 一次导入请求的参数：用户确认过升级报告后，之后的请求（含编号冲突的处理）都带上确认。 */
interface ImportAttempt {
  file: File;
  policy: ImportConflictPolicy;
  confirmReport: boolean;
}

function hasDiagnostics(diagnostics: ImportFailureDiagnostics | undefined): diagnostics is ImportFailureDiagnostics {
  return !!diagnostics && diagnostics.blocking.length + diagnostics.auto_fixable.length + diagnostics.warnings.length > 0;
}

/**
 * 导入项目 ZIP：选择文件、导入包升级有报告时请用户确认、处理项目编号冲突、展示导入诊断，导入成功后进入项目。
 * `element` 包含隐藏的文件输入与各个对话框，渲染在页面里一次即可。
 */
export function useProjectImport({ onImported }: { onImported: () => void }): {
  importing: boolean;
  openPicker: () => void;
  element: ReactNode;
} {
  const { t } = useTranslation("dashboard");
  const [, navigate] = useLocation();
  const inputRef = useRef<HTMLInputElement>(null);
  // 大厅卸载时 abort；导入请求本身不接这个 signal（它是写入请求，abort 只会断开客户端），
  // 仅在 await 之后据此判断用户是否已离开大厅。
  const unmountAbortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    unmountAbortRef.current = controller;
    return () => controller.abort();
  }, []);
  const [importing, setImporting] = useState(false);
  const [conflict, setConflict] = useState<{ attempt: ImportAttempt; projectName: string } | null>(null);
  const [reportConfirm, setReportConfirm] = useState<{ attempt: ImportAttempt; pending: PendingImportReport } | null>(
    null,
  );
  const [diagnostics, setDiagnostics] = useState<ImportDiagnosticsState | null>(null);

  const importFile = async (attempt: ImportAttempt) => {
    const { file, policy, confirmReport } = attempt;
    const leftSignal = unmountAbortRef.current?.signal;
    const { pushToast } = useAppStore.getState();
    setImporting(true);
    try {
      const result = await API.importProject(file, policy, { confirmReport });
      const left = leftSignal?.aborted ?? false;
      if (!left) {
        setConflict(null);
        setReportConfirm(null);
      }
      onImported();

      const navigateTo = `/app/projects/${result.project_name}`;
      const { auto_fixed: autoFixed, warnings } = result.diagnostics;
      const hasFindings = autoFixed.length > 0 || warnings.length > 0;
      if (!hasFindings && !left) {
        navigate(navigateTo);
        return;
      }
      const title = getProjectDisplayName(result.project.title, t("untitled_project"));
      const text =
        autoFixed.length > 0
          ? t("import_auto_fixed", { title, count: autoFixed.length })
          : t("lobby_import_succeeded", { title });
      if (left) {
        // 用户已不在大厅：不把人拉回新项目，也没有对话框可展示诊断，结果改以带「打开项目」按钮的提示告知。
        pushToast(text, "success", {
          action: { label: t("lobby_import_open_project"), onClick: () => navigate(navigateTo) },
        });
        return;
      }
      pushToast(text, "success");
      setDiagnostics({
        source: "success",
        diagnostics: { blocking: [], auto_fixable: autoFixed, warnings },
        navigateTo,
      });
    } catch (err) {
      const error = err as ImportError;
      const left = leftSignal?.aborted ?? false;
      if (error.status === 409 && error.confirmation_required && error.migration_report && !confirmReport) {
        if (left) {
          pushToast(t("lobby_import_confirm_left"), "warning");
          return;
        }
        setReportConfirm({
          attempt,
          pending: { report: error.migration_report, episodes: error.migration_report_episodes ?? [] },
        });
        return;
      }
      if (error.status === 409 && error.conflict_project_name && policy === "prompt" && left) {
        pushToast(t("lobby_import_conflict_left", { name: error.conflict_project_name }), "warning");
        return;
      }
      if (left) {
        pushToast(t("lobby_import_failed_left", { message: error.message }), "error");
        return;
      }
      setReportConfirm(null);
      if (error.status === 409 && error.conflict_project_name && policy === "prompt") {
        setConflict({ attempt, projectName: error.conflict_project_name });
        return;
      }
      setConflict(null);
      // 接口总会带回一份诊断，没有任何条目时对话框不显示内容，改用提示说明失败原因。
      if (hasDiagnostics(error.diagnostics)) {
        setDiagnostics({ source: "failure", diagnostics: error.diagnostics });
      } else {
        pushToast(t("lobby_import_failed", { message: error.message }), "error");
      }
    } finally {
      setImporting(false);
    }
  };

  const handleFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) void importFile({ file, policy: "prompt", confirmReport: false });
  };

  const element = (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".zip,application/zip"
        aria-label={t("import_project_file_aria")}
        onChange={handleFile}
        className="hidden"
      />
      <ImportReportConfirmDialog
        pending={reportConfirm?.pending ?? null}
        importing={importing}
        onConfirm={() => {
          if (reportConfirm) void importFile({ ...reportConfirm.attempt, confirmReport: true });
        }}
        onCancel={() => setReportConfirm(null)}
      />
      <ImportConflictDialog
        projectName={conflict?.projectName ?? null}
        importing={importing}
        onResolve={(policy) => {
          if (conflict) void importFile({ ...conflict.attempt, policy });
        }}
        onCancel={() => setConflict(null)}
      />
      {diagnostics && (
        <ArchiveDiagnosticsDialog
          title={t(diagnostics.source === "failure" ? "import_failure_diagnostics" : "import_diagnostics")}
          description={t(
            diagnostics.source === "failure" ? "import_failure_with_diagnostics" : "import_success_with_diagnostics",
          )}
          sections={[
            {
              key: "blocking",
              title: t("blocking_issues"),
              severity: "blocking",
              items: diagnostics.diagnostics.blocking,
            },
            {
              key: "auto_fixed",
              title: t("auto_fixed_issues"),
              severity: "auto_fixed",
              items: diagnostics.diagnostics.auto_fixable,
            },
            {
              key: "warnings",
              title: t("diagnostics_warnings"),
              severity: "warnings",
              items: diagnostics.diagnostics.warnings,
            },
          ]}
          onClose={() => {
            const target = diagnostics.source === "success" ? diagnostics.navigateTo : null;
            setDiagnostics(null);
            if (target) navigate(target);
          }}
        />
      )}
    </>
  );

  return { importing, openPicker: () => inputRef.current?.click(), element };
}
