import { AlertTriangle } from "lucide-react";
import { useId } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import type { ConfigIssue } from "@/stores/config-status-store";

/**
 * Show configuration problems in their own section only. Settings places notices atop full-bleed sections;
 * width-limited sections place them after their header explanation. Use a single problem as the title, or list all problems when multiple exist.
 */
export function ConfigIssueNotice({ issues }: { issues: ConfigIssue[] }) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  if (issues.length === 0) return null;
  const [only] = issues;
  return (
    // This static notice exists when the page opens; avoid Alert's default role="alert" so screen readers do not interrupt immediately
    <Alert role="note" aria-labelledby={titleId}>
      <AlertTriangle aria-hidden />
      <AlertTitle id={titleId}>{t(issues.length === 1 ? only.label : "config_incomplete")}</AlertTitle>
      {issues.length === 1 ? (
        only.description && <AlertDescription>{t(only.description)}</AlertDescription>
      ) : (
        <AlertDescription>
          <ul className="flex flex-col gap-0.5">
            {issues.map((issue) => (
              <li key={issue.key}>{t(issue.label)}</li>
            ))}
          </ul>
        </AlertDescription>
      )}
    </Alert>
  );
}
