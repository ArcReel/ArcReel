import { CircleAlert, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { CARD_STYLE } from "@/components/ui/darkroom-tokens";
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
      <div
        aria-live="polite"
        className="mb-5 rounded-[10px] border border-hairline px-4 py-2.5 text-[12.5px] text-text-2"
        style={CARD_STYLE}
      >
        {t("ce_diagnostics_clean")}
      </div>
    );
  }

  return (
    <div className="mb-5 overflow-hidden rounded-[10px] border border-hairline" style={CARD_STYLE}>
      <div
        aria-live="polite"
        className="border-b border-hairline-soft px-4 py-2.5 text-[12.5px] font-medium text-text"
      >
        {t("ce_diagnostics_summary", { errors: errors.length, warnings: warnings.length })}
      </div>
      {issues.map(({ issue, level }) => {
        const section = sectionOfIssuePath(issue.path);
        return (
          <button
            key={`${level}-${issue.path}-${issue.code}`}
            type="button"
            onClick={() => onLocate(section)}
            className="flex w-full items-start gap-2.5 border-b border-hairline-soft px-4 py-2.5 text-left transition-colors last:border-b-0 hover:bg-bg-grad-a/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            {level === "error" ? (
              <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warm-bright" aria-hidden />
            ) : (
              <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-text-3" aria-hidden />
            )}
            <span className="min-w-0 flex-1 text-[12.5px] leading-[1.55] text-text-2">
              <span className="mr-2 text-text-3">
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
