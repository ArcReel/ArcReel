import { useTranslation } from "react-i18next";
import type { ProviderCheckpoint } from "@/types/workflow";

interface Props {
  checkpoint: ProviderCheckpoint;
  /** 供应商侧作业号；进行中的任务给出它，便于对着供应商控制台核对。 */
  showJobId?: boolean;
}

/**
 * 供应商侧是否已经收单。
 *
 * 它自己占一行，不折进任务状态词里：已收单意味着重试可能重复计费，而任务状态词回答的是
 * 另一个问题（这次尝试跑到哪了）。未收单时不出现——没有这条事实就不摆一句空话。
 */
export function CheckpointNote({ checkpoint, showJobId = false }: Props) {
  const { t } = useTranslation("workflow");
  if (!checkpoint.submitted) return null;
  const jobId = showJobId ? checkpoint.provider_job_id : null;
  return (
    <span
      className="flex flex-wrap items-baseline gap-x-1 text-[11px]"
      style={{ color: "var(--color-text-3)" }}
    >
      <span>
        {t("checkpoint_submitted", {
          provider: checkpoint.provider_id ?? t("checkpoint_provider_unknown"),
        })}
      </span>
      {jobId && (
        <code translate="no" className="break-all font-mono">
          {jobId}
        </code>
      )}
    </span>
  );
}
