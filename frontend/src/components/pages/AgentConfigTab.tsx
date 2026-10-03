import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { AgentLanguageRuleSection } from "@/components/agent/AgentLanguageRuleSection";
import { AgentMemoryCabinet } from "@/components/agent/AgentMemoryCabinet";
import { AgentPageIntro } from "@/components/agent/AgentPageIntro";
import { CredentialsSection } from "@/components/agent/CredentialsSection";
import { GHOST_BTN_CLS, INPUT_CLS } from "@/components/shared/darkroom-tokens";
import { FieldLabel } from "@/components/shared/FieldLabel";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { SectionShell } from "@/components/shared/SectionShell";
import { useConfigStatusStore } from "@/stores/config-status-store";
import type { GetSystemConfigResponse, SystemConfigPatch } from "@/types";
import { errMsg, voidCall } from "@/utils/async";

import { ExternalAgentModal } from "./ExternalAgentModal";

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

interface AgentConfigTabProps {
  visible: boolean;
}

export function AgentConfigTab({ visible }: AgentConfigTabProps) {
  const { t } = useTranslation("dashboard");
  const [remoteData, setRemoteData] = useState<GetSystemConfigResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showExternalGuide, setShowExternalGuide] = useState(false);

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
  const saving = unit.status === "saving";
  const updateField = (key: keyof AgentRuntimeFields, value: string) =>
    unit.setValue((prev) => ({ ...prev, [key]: value }));

  if (loadError) {
    return (
      <div className={visible ? "px-1 py-8" : "hidden"}>
        <div
          role="alert"
          className="flex items-start gap-1.5 rounded-md border px-4 py-3 text-[12.5px]"
          style={{
            borderColor: "color-mix(in oklab, var(--warn) 30%, transparent)",
            background: "color-mix(in oklab, var(--warn) 15%, transparent)",
            color: "var(--warn)",
          }}
        >
          <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{t("load_failed", { message: loadError })}</span>
        </div>
        <button type="button" onClick={() => void load()} className={`${GHOST_BTN_CLS} mt-3`}>
          <Loader2 className="h-3.5 w-3.5" aria-hidden />
          {t("common:retry")}
        </button>
      </div>
    );
  }

  if (!remoteData) {
    return (
      <div
        className={
          visible
            ? "flex items-center gap-2 px-1 py-12 text-muted-foreground"
            : "hidden"
        }
      >
        <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-primary" aria-hidden />
        <span className="font-mono text-[11px] uppercase tracking-[0.14em]">
          {t("common:loading")}
        </span>
      </div>
    );
  }

  return (
    <div className={visible ? undefined : "hidden"}>
      <div className="space-y-7 pb-0 pt-1">
        <AgentPageIntro onOpenExternalGuide={() => setShowExternalGuide(true)} />
        <CredentialsSection />
        <SectionShell kicker="Runtime Tuning" title={t("advanced_settings")}>
          <div className="space-y-4">
            <div>
              <FieldLabel htmlFor="agent-cleanup-delay" className="">
                {t("session_cleanup_delay_label")}
              </FieldLabel>
              <p className="mt-0.5 text-[11.5px] text-muted-foreground">
                {t("session_cleanup_delay_desc")}
              </p>
              <input
                id="agent-cleanup-delay"
                type="number"
                min={10}
                max={3600}
                value={unit.value.cleanupDelaySeconds}
                onChange={(e) => updateField("cleanupDelaySeconds", e.target.value)}
                className={`${INPUT_CLS} mt-1.5 max-w-[140px]`}
                disabled={saving}
              />
            </div>
            <div>
              <FieldLabel htmlFor="agent-max-sessions" className="">
                {t("max_concurrent_sessions_label")}
              </FieldLabel>
              <p className="mt-0.5 text-[11.5px] text-muted-foreground">
                {t("max_concurrent_sessions_desc")}
              </p>
              <input
                id="agent-max-sessions"
                type="number"
                min={1}
                max={20}
                value={unit.value.maxConcurrentSessions}
                onChange={(e) => updateField("maxConcurrentSessions", e.target.value)}
                className={`${INPUT_CLS} mt-1.5 max-w-[140px]`}
                disabled={saving}
              />
            </div>
          </div>
        </SectionShell>
        <AgentLanguageRuleSection />
        <AgentMemoryCabinet scope={{ level: "user" }} frame="section" />
      </div>

      {/* 设置页内容区是滚动容器，保存栏吸底常驻 */}
      <SaveBar unit={unit} className="sticky bottom-0" />
      {showExternalGuide && (
        <ExternalAgentModal onClose={() => setShowExternalGuide(false)} />
      )}
    </div>
  );
}
