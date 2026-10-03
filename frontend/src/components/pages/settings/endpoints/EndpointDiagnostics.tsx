import { CircleAlert, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { EndpointDefinitionIssue } from "@/types";
import { sectionOfIssuePath, type EndpointFormSection } from "./endpoint-definition-draft";

const SECTION_TITLE_KEY: Record<EndpointFormSection, string> = {
  meta: "ce_section_meta",
  auth: "ce_section_auth",
  inputs: "ce_section_inputs",
  submit: "ce_section_submit",
  poll: "ce_section_poll",
  status: "ce_section_status",
  capabilities: "ce_section_capabilities",
  test: "ce_section_test",
};

/**
 * 诊断卡：与保存、导入共用同一个服务端校验器，常驻头部下方。
 * 每条标注所属分节，点击滚动到该节。
 */
export function EndpointDiagnostics({
  errors,
  warnings,
  onLocate,
}: {
  errors: EndpointDefinitionIssue[];
  warnings: EndpointDefinitionIssue[];
  onLocate: (section: EndpointFormSection | null) => void;
}) {
  const { t } = useTranslation("dashboard");
  const issues = [
    ...errors.map((issue) => ({ issue, level: "error" as const })),
    ...warnings.map((issue) => ({ issue, level: "warning" as const })),
  ];

  if (issues.length === 0) {
    return (
      <div aria-live="polite" className="mb-5 rounded-lg border border-border px-4 py-2.5 text-sm text-subtle-foreground">
        {t("ce_diagnostics_clean")}
      </div>
    );
  }

  return (
    <div className="mb-5 overflow-hidden rounded-lg border border-border">
      <div aria-live="polite" className="border-b border-border px-4 py-2.5 text-sm font-medium text-foreground">
        {t("ce_diagnostics_summary", { errors: errors.length, warnings: warnings.length })}
      </div>
      {issues.map(({ issue, level }) => {
        const section = sectionOfIssuePath(issue.path);
        return (
          <button
            key={`${level}-${issue.path}-${issue.code}`}
            type="button"
            onClick={() => onLocate(section)}
            className="flex w-full items-start gap-2.5 border-b border-border/50 px-4 py-2.5 text-left transition-colors outline-none last:border-b-0 hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset"
          >
            {level === "error" ? (
              <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
            ) : (
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
            )}
            <span className="min-w-0 flex-1 text-sm text-subtle-foreground">
              <span className="mr-2 text-muted-foreground">
                {section === null ? t("ce_view_json") : t(SECTION_TITLE_KEY[section])}
              </span>
              {issue.message}
            </span>
          </button>
        );
      })}
    </div>
  );
}
