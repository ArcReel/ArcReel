import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronRight, Loader2, RotateCw } from "lucide-react";
import { Trans, useTranslation } from "react-i18next";
import { Link } from "wouter";

import { API } from "@/api";
import { settingsSectionPath } from "@/app-routes";
import { CredentialsSection } from "@/components/agent/CredentialsSection";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { PageShellFooter } from "@/components/shared/page-shell/PageShell";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { useConfigStatusStore, useSectionConfigIssues } from "@/stores/config-status-store";
import type { GetSystemConfigResponse, SystemConfigPatch } from "@/types";
import { errMsg, voidCall } from "@/utils/async";

import { ConfigIssueNotice } from "./settings/ConfigIssueNotice";

/** Agent 会话启动时拼进系统提示的语言规范模版，正文在「提示词模版」中查看。 */
const AGENT_LANGUAGE_RULE_ID = "text/agent_language_rule";

/** 运行参数编辑单元：输入框里的原始字符串，保存时再转成数字。 */
interface AgentRuntimeFields {
  cleanupDelaySeconds: string;
  maxConcurrentSessions: string;
}

const DEFAULT_FIELDS: AgentRuntimeFields = {
  cleanupDelaySeconds: "300",
  maxConcurrentSessions: "5",
};

function fieldsFrom(data: GetSystemConfigResponse): AgentRuntimeFields {
  const s = data.settings;
  return {
    cleanupDelaySeconds: String(s.agent_session_cleanup_delay_seconds ?? 300),
    maxConcurrentSessions: String(s.agent_max_concurrent_sessions ?? 5),
  };
}

function buildPatch(fields: AgentRuntimeFields, saved: AgentRuntimeFields): SystemConfigPatch {
  const patch: SystemConfigPatch = {};
  if (fields.cleanupDelaySeconds !== saved.cleanupDelaySeconds)
    patch.agent_session_cleanup_delay_seconds = Number(fields.cleanupDelaySeconds) || 300;
  if (fields.maxConcurrentSessions !== saved.maxConcurrentSessions)
    patch.agent_max_concurrent_sessions = Number(fields.maxConcurrentSessions) || 5;
  return patch;
}

/**
 * 全局设置「ArcReel Agent」：页头说明 → Agent 供应商列表 → 默认折叠的「高级」运行参数 → 语言规范说明。
 * Agent 供应商的增改删与切换生效是即时动作；只有运行参数进入编辑单元，由外壳底行的保存栏提交。
 */
export function AgentConfigTab() {
  const { t } = useTranslation("dashboard");
  const issues = useSectionConfigIssues("arcreel-agent");

  return (
    <section aria-labelledby="arcreel-agent-title" className="flex flex-col gap-8">
      <header className="flex flex-col gap-1">
        <h2 id="arcreel-agent-title" className="text-lg font-medium">
          {t("settings_arcreel_agent")}
        </h2>
        <p className="text-sm text-muted-foreground">{t("arcreel_agent_section_desc")}</p>
        <p className="text-sm text-muted-foreground">
          <Trans
            t={t}
            i18nKey="arcreel_agent_external_hint"
            components={{
              guide: <Link href={settingsSectionPath("external-agent")} className="text-primary underline-offset-4 hover:underline" />,
            }}
          />
        </p>
      </header>
      <ConfigIssueNotice issues={issues} />
      <CredentialsSection />
      <RuntimeSettings />
      <p className="text-sm text-muted-foreground">
        <Trans
          t={t}
          i18nKey="arcreel_agent_language_rule_hint"
          components={{
            templates: (
              <Link
                href={settingsSectionPath("prompt-templates", { template: AGENT_LANGUAGE_RULE_ID })}
                className="text-primary underline-offset-4 hover:underline"
              />
            ),
          }}
        />
      </p>
    </section>
  );
}

function RuntimeSettings() {
  const { t } = useTranslation("dashboard");
  const [remoteData, setRemoteData] = useState<GetSystemConfigResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setRemoteData(await API.getSystemConfig());
    } catch (err) {
      setLoadError(errMsg(err));
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount 时异步拉取配置后回写，属于受控的初始化加载
    void load();
  }, [load]);

  const source = useMemo(() => (remoteData ? fieldsFrom(remoteData) : DEFAULT_FIELDS), [remoteData]);
  const saveFields = useCallback(async (fields: AgentRuntimeFields, saved: AgentRuntimeFields) => {
    const res = await API.updateSystemConfig(buildPatch(fields, saved));
    setRemoteData(res);
    voidCall(useConfigStatusStore.getState().refresh());
    return fieldsFrom(res);
  }, []);
  const unit = useEditUnit({ source, save: saveFields });
  const disabled = !remoteData || unit.status === "saving";
  const updateField = (key: keyof AgentRuntimeFields, value: string) =>
    unit.setValue((prev) => ({ ...prev, [key]: value }));

  return (
    <div className="rounded-lg border border-border p-1">
      <Collapsible>
        <CollapsibleTrigger render={<Button variant="ghost" className="w-full justify-start" />}>
          <ChevronRight
            aria-hidden
            data-icon="inline-start"
            className="text-muted-foreground transition-transform duration-fast in-data-panel-open:rotate-90"
          />
          {t("arcreel_agent_advanced")}
          {unit.dirty && <span className="sr-only">{t("common:unsaved_changes")}</span>}
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="flex flex-col gap-5 px-3 pt-2 pb-3">
            {loadError ? (
              <div className="flex flex-col items-start gap-3">
                <p role="alert" className="text-sm text-destructive">
                  {t("load_failed", { message: loadError })}
                </p>
                <Button variant="outline" size="sm" onClick={() => void load()}>
                  <RotateCw aria-hidden data-icon="inline-start" />
                  {t("common:retry")}
                </Button>
              </div>
            ) : (
              <>
                {!remoteData && <Loader2 aria-hidden className="size-4 animate-spin text-muted-foreground" />}
                <NumberField
                  id="agent-cleanup-delay"
                  label={t("session_cleanup_delay_label")}
                  description={t("session_cleanup_delay_desc")}
                  min={10}
                  max={3600}
                  value={unit.value.cleanupDelaySeconds}
                  onChange={(v) => updateField("cleanupDelaySeconds", v)}
                  disabled={disabled}
                />
                <NumberField
                  id="agent-max-sessions"
                  label={t("max_concurrent_sessions_label")}
                  description={t("max_concurrent_sessions_desc")}
                  min={1}
                  max={20}
                  value={unit.value.maxConcurrentSessions}
                  onChange={(v) => updateField("maxConcurrentSessions", v)}
                  disabled={disabled}
                />
              </>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
      <PageShellFooter>
        <SaveBar unit={unit} />
      </PageShellFooter>
    </div>
  );
}

function NumberField({
  id,
  label,
  description,
  min,
  max,
  value,
  onChange,
  disabled,
}: {
  id: string;
  label: string;
  description: string;
  min: number;
  max: number;
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <p id={`${id}-desc`} className="text-sm text-muted-foreground">
        {description}
      </p>
      <Input
        id={id}
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-describedby={`${id}-desc`}
        disabled={disabled}
        className="w-36"
      />
    </div>
  );
}
