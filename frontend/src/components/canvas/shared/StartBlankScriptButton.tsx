import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FilePlus2 } from "lucide-react";
import { API } from "@/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";

interface Props {
  projectName: string;
  episode: number;
  /** 本集有未确认的脚本规划或待修复草稿：先确认弃置再建正式脚本。 */
  discardsPlan: boolean;
  className?: string;
}

/**
 * 「从空白开始」：本集没有正式脚本时建出空的正式脚本，之后在时间线上逐条添加分镜。
 * 成功后刷新项目，集页随正式脚本出现切到时间线。
 */
export function StartBlankScriptButton({ projectName, episode, discardsPlan, className = "" }: Props) {
  const { t } = useTranslation("dashboard");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const start = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await API.startBlankScript(projectName, episode);
      setConfirmOpen(false);
      await useProjectsStore.getState().refreshProject(projectName);
    } catch (err) {
      useAppStore.getState().pushToast(t("blank_script_failed", { message: errMsg(err) }), "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <button
        type="button"
        className={`inline-flex items-center gap-1.5 disabled:cursor-not-allowed disabled:opacity-50 ${className}`.trim()}
        disabled={submitting}
        title={t("blank_script_hint")}
        onClick={() => (discardsPlan ? setConfirmOpen(true) : void start())}
      >
        <FilePlus2 className="h-3.5 w-3.5" aria-hidden="true" />
        <span>{t("blank_script_start")}</span>
      </button>
      {discardsPlan && (
        <ConfirmDialog
          open={confirmOpen}
          title={t("blank_script_discard_title")}
          description={t("blank_script_discard_desc")}
          confirmLabel={t("blank_script_start")}
          tone="danger"
          loading={submitting}
          onConfirm={() => void start()}
          onCancel={() => setConfirmOpen(false)}
        />
      )}
    </>
  );
}

/** 没有脚本规划可走的集（广告/短片）在没有正式脚本时的画布：说明现状，并给出「从空白开始」。 */
export function NoScriptBlankState({ projectName, episode, className = "" }: { projectName: string; episode: number; className?: string }) {
  const { t } = useTranslation("dashboard");
  return (
    <div className={`flex flex-col items-center justify-center gap-3 ${className}`.trim()} style={{ color: "var(--color-text-4)" }}>
      <p className="m-0">{t("timeline_no_script_blank_hint")}</p>
      <StartBlankScriptButton
        projectName={projectName}
        episode={episode}
        discardsPlan={false}
        className="arc-btn-primary focus-ring rounded-lg px-4 py-2 text-[12.5px] font-semibold"
      />
    </div>
  );
}
