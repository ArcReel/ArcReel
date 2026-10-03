import { useRef, useState, type ChangeEvent, type ReactNode } from "react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { ArchiveDiagnosticsDialog } from "@/components/shared/ArchiveDiagnosticsDialog";
import { useAppStore } from "@/stores/app-store";
import { getProjectDisplayName } from "@/utils/project-display";
import type { ImportConflictPolicy, ImportFailureDiagnostics } from "@/types";
import { ImportConflictDialog } from "./ImportConflictDialog";

type ImportDiagnosticsState =
  | { source: "success"; diagnostics: ImportFailureDiagnostics; navigateTo: string }
  | { source: "failure"; diagnostics: ImportFailureDiagnostics };

type ImportError = Error & {
  status?: number;
  conflict_project_name?: string;
  diagnostics?: ImportFailureDiagnostics;
};

function hasDiagnostics(diagnostics: ImportFailureDiagnostics | undefined): diagnostics is ImportFailureDiagnostics {
  return !!diagnostics && diagnostics.blocking.length + diagnostics.auto_fixable.length + diagnostics.warnings.length > 0;
}

/**
 * 导入项目 ZIP：选择文件、处理项目编号冲突、展示导入诊断，导入成功后进入项目。
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
  const [importing, setImporting] = useState(false);
  const [conflict, setConflict] = useState<{ file: File; projectName: string } | null>(null);
  const [diagnostics, setDiagnostics] = useState<ImportDiagnosticsState | null>(null);

  const importFile = async (file: File, policy: ImportConflictPolicy = "prompt") => {
    setImporting(true);
    try {
      const result = await API.importProject(file, policy);
      setConflict(null);
      onImported();

      const navigateTo = `/app/projects/${result.project_name}`;
      const { auto_fixed: autoFixed, warnings } = result.diagnostics;
      if (autoFixed.length === 0 && warnings.length === 0) {
        navigate(navigateTo);
        return;
      }
      const title = getProjectDisplayName(result.project.title, t("untitled_project"));
      useAppStore
        .getState()
        .pushToast(
          autoFixed.length > 0
            ? t("import_auto_fixed", { title, count: autoFixed.length })
            : t("lobby_import_succeeded", { title }),
          "success",
        );
      setDiagnostics({
        source: "success",
        diagnostics: { blocking: [], auto_fixable: autoFixed, warnings },
        navigateTo,
      });
    } catch (err) {
      const error = err as ImportError;
      if (error.status === 409 && error.conflict_project_name && policy === "prompt") {
        setConflict({ file, projectName: error.conflict_project_name });
        return;
      }
      setConflict(null);
      // 接口总会带回一份诊断，没有任何条目时对话框不显示内容，改用提示说明失败原因。
      if (hasDiagnostics(error.diagnostics)) {
        setDiagnostics({ source: "failure", diagnostics: error.diagnostics });
      } else {
        useAppStore.getState().pushToast(t("lobby_import_failed", { message: error.message }), "error");
      }
    } finally {
      setImporting(false);
    }
  };

  const handleFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) void importFile(file);
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
      <ImportConflictDialog
        projectName={conflict?.projectName ?? null}
        importing={importing}
        onResolve={(policy) => {
          if (conflict) void importFile(conflict.file, policy);
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
