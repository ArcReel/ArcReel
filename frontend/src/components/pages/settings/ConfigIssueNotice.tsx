import { AlertTriangle, ChevronDown } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { useMobileTier } from "@/hooks/useMobileTier";
import type { ConfigIssue } from "@/stores/config-status-store";

/**
 * 配置问题就地提示：只出现在问题所属分区。全出血分区由设置页放在分区顶部，
 * 限宽分区由分区自己放在页头说明之后。只有一条问题时以它为标题，多条时列出全部。
 *
 * 移动档默认收起：一屏高度有限，先只占一行「配置不完整」，展开再看逐条原因。
 * 桌面档默认展开——提示通常只有一两行，收起来反而多一次点击。
 */
export function ConfigIssueNotice({ issues }: { issues: ConfigIssue[] }) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  const mobile = useMobileTier();
  const [open, setOpen] = useState(!mobile);
  if (issues.length === 0) return null;
  const [only] = issues;
  const title = t(issues.length === 1 ? only.label : "config_incomplete");

  // 没有展开内容（单条问题又没有说明）时不做折叠：折叠一个空面板只会让人白点一次。
  const hasDetails = issues.length > 1 || Boolean(only.description);
  if (!hasDetails) {
    return (
      // 页面打开时就在的静态提示，不用 Alert 默认的 role="alert"，以免读屏立即打断播报
      <Alert role="note" aria-labelledby={titleId}>
        <AlertTriangle aria-hidden />
        <AlertTitle id={titleId}>{title}</AlertTitle>
      </Alert>
    );
  }

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      {/* items-center：折叠标题行在移动端被抬到 44px 命中区，图标不居中会浮在文字上方。 */}
      <Alert role="note" aria-labelledby={titleId} className="items-center">
        <AlertTriangle aria-hidden />
        <AlertTitle id={titleId}>
          <CollapsibleTrigger
            render={
              <button
                type="button"
                className="group/button flex w-full items-center gap-1 text-left"
              />
            }
          >
            <span className="min-w-0 flex-1">{title}</span>
            <ChevronDown
              aria-hidden
              className="size-4 shrink-0 text-muted-foreground transition-transform group-aria-expanded/button:rotate-180"
            />
          </CollapsibleTrigger>
        </AlertTitle>
        <CollapsibleContent>
          <AlertDescription>
            {issues.length === 1 && only.description ? (
              t(only.description)
            ) : (
              <ul className="flex flex-col gap-0.5">
                {issues.map((issue) => (
                  <li key={issue.key}>{t(issue.label)}</li>
                ))}
              </ul>
            )}
          </AlertDescription>
        </CollapsibleContent>
      </Alert>
    </Collapsible>
  );
}
