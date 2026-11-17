import { useTranslation } from "react-i18next";
import { AlertTriangle } from "lucide-react";

import { DefaultModelSettingsLink } from "@/components/shared/DefaultModelSettingsLink";
import { OutputTruncationHint } from "@/components/shared/OutputTruncationHint";
import { TaskFailureDetails } from "@/components/shared/TaskFailureDetails";

import type { PlanningFailure } from "./episode-planning-model";

/**
 * 上一次分集规划停在失败上时的原因与出路：未设置默认模型时给设置入口，输出被截断时给登记最大输出长度的入口，
 * 服务端原文收进可展开的「详情」。分集规划面板与重新规划的方案栏共用。任务没带回原因时不显示原因这一句，
 * 也没有出路时整块不显示。
 */
export function PlanningFailureNote({ failure }: { failure: PlanningFailure }) {
  const { t } = useTranslation("dashboard");
  const reason = failure.message.trim();
  const settings = failure.code === "text_default_model_not_set";
  if (!reason && !settings && !failure.truncated) return null;
  return (
    <div role="alert" className="flex flex-col gap-1.5 rounded-md bg-warn/10 p-2 text-xs">
      {reason ? (
        <p className="flex gap-1.5 text-subtle-foreground">
          <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0 text-warn" />
          <span className="min-w-0 wrap-break-word">{t("episode_planning_failed", { reason })}</span>
        </p>
      ) : null}
      {settings ? <DefaultModelSettingsLink /> : null}
      {failure.truncated ? <OutputTruncationHint truncation={failure.truncated} /> : null}
      <TaskFailureDetails code={failure.code} params={failure.params} detail={failure.detail} />
    </div>
  );
}
