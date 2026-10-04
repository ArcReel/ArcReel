import { useId, useState, type ReactNode } from "react";
import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";

import { settingsSectionPath } from "@/app-routes";
import { Button, buttonVariants } from "@/components/ui/button";

import { CopyableValue } from "./CopyableValue";
import { CreateAccessTokenDialog } from "./CreateAccessTokenDialog";

const MCP_ENDPOINT = `${window.location.origin}/mcp`;
const INSTALL_COMMAND = "npx skills add ArcReel/skills";
const SETUP_SKILL = "setup-arcreel-skills";
const INSTALL_GUIDE_URL = `${window.location.origin}/agent-installation-guide.md`;

/**
 * 全局设置「外部 Agent 接入」：按安装公开 skills、配置远程 MCP、准备访问令牌三步展示，
 * 之后给出把提示词交给 AI Agent 代为接入的做法。第三步就地创建访问令牌；令牌列表只在「访问令牌」分区管理。
 */
export function ExternalAgentSection() {
  const { t } = useTranslation("dashboard");
  const [createOpen, setCreateOpen] = useState(false);
  const stepsLabelId = useId();
  const agentPrompt = t("ext_agent_prompt", { guideUrl: INSTALL_GUIDE_URL });

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-1">
        <h2 className="text-lg font-medium">{t("settings_external_agent")}</h2>
        <p className="max-w-[40em] text-sm text-muted-foreground">{t("ext_agent_desc")}</p>
      </div>

      <section aria-labelledby={stepsLabelId} className="flex flex-col gap-3">
        <h3 id={stepsLabelId} className="sr-only">
          {t("ext_agent_steps")}
        </h3>
        <ol className="flex flex-col divide-y divide-border rounded-lg border border-border bg-card">
          <Step index={1} title={t("ext_agent_step_skills_title")} description={t("ext_agent_step_skills_desc")}>
            <CopyableValue
              label={t("ext_agent_install_command")}
              value={INSTALL_COMMAND}
              copyLabel={t("ext_agent_copy_install_command")}
            />
          </Step>
          <Step index={2} title={t("ext_agent_step_mcp_title")} description={t("ext_agent_step_mcp_desc")}>
            <CopyableValue
              label={t("ext_agent_setup_skill")}
              value={SETUP_SKILL}
              copyLabel={t("ext_agent_copy_setup_skill")}
            />
            <CopyableValue
              label={t("ext_agent_mcp_endpoint")}
              value={MCP_ENDPOINT}
              copyLabel={t("ext_agent_copy_mcp_endpoint")}
            />
          </Step>
          <Step index={3} title={t("ext_agent_step_token_title")} description={t("ext_agent_step_token_desc")}>
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={() => setCreateOpen(true)}>
                <Plus aria-hidden data-icon="inline-start" />
                {t("access_token_create")}
              </Button>
              {/* 跳到另一个分区是导航，渲染为链接而不是 button */}
              <Link href={settingsSectionPath("access-tokens")} className={buttonVariants({ variant: "ghost" })}>
                {t("ext_agent_manage_tokens")}
              </Link>
            </div>
          </Step>
        </ol>
      </section>

      <section className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <h3 className="text-sm font-medium">{t("ext_agent_prompt_title")}</h3>
          <p className="max-w-[40em] text-sm text-muted-foreground">{t("ext_agent_prompt_desc")}</p>
        </div>
        <CopyableValue value={agentPrompt} copyLabel={t("ext_agent_copy_prompt")} multiline />
      </section>

      <CreateAccessTokenDialog open={createOpen} onOpenChange={setCreateOpen} />
    </div>
  );
}

function Step({
  index,
  title,
  description,
  children,
}: {
  index: number;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <li className="flex gap-4 p-4">
      <span
        aria-hidden
        className="grid size-6 shrink-0 place-items-center rounded-full border border-primary/40 bg-primary/10 text-xs font-medium text-primary tabular-nums"
      >
        {index}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <div className="flex flex-col gap-1">
          <h4 className="text-sm font-medium">{title}</h4>
          <p className="text-sm text-muted-foreground">{description}</p>
        </div>
        {children}
      </div>
    </li>
  );
}
