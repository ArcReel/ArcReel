import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { AgentPageIntro } from "@/components/agent/AgentPageIntro";
import { CredentialsSection } from "@/components/agent/CredentialsSection";
import { GHOST_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { FieldLabel } from "@/components/ui/FieldLabel";
import { SectionShell } from "@/components/ui/SectionShell";
import { useWarnUnsaved } from "@/hooks/useWarnUnsaved";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import type { GetSystemConfigResponse, SystemConfigPatch } from "@/types";
import { errMsg, voidCall } from "@/utils/async";

import { TabSaveFooter } from "./TabSaveFooter";

interface AgentDraft {
  cleanupDelaySeconds: string;
  maxConcurrentSessions: string;
}

function buildDraft(data: GetSystemConfigResponse): AgentDraft {
  const s = data.settings;
  return {
    cleanupDelaySeconds: String(s.agent_session_cleanup_delay_seconds ?? 300),
    maxConcurrentSessions: String(s.agent_max_concurrent_sessions ?? 5),
  };
}

function deepEqual(a: AgentDraft, b: AgentDraft): boolean {
  return (
    a.cleanupDelaySeconds === b.cleanupDelaySeconds &&
    a.maxConcurrentSessions === b.maxConcurrentSessions
  );
}

function buildPatch(draft: AgentDraft, saved: AgentDraft): SystemConfigPatch {
  const patch: SystemConfigPatch = {};
  if (draft.cleanupDelaySeconds !== saved.cleanupDelaySeconds)
    patch.agent_session_cleanup_delay_seconds = Number(draft.cleanupDelaySeconds) || 300;
  if (draft.maxConcurrentSessions !== saved.maxConcurrentSessions)
    patch.agent_max_concurrent_sessions = Number(draft.maxConcurrentSessions) || 5;
  return patch;
}

interface AgentConfigTabProps {
  visible: boolean;
}

export function AgentConfigTab({ visible }: AgentConfigTabProps) {
  const { t } = useTranslation("dashboard");
  const [remoteData, setRemoteData] = useState<GetSystemConfigResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<AgentDraft>({
    cleanupDelaySeconds: "300",
    maxConcurrentSessions: "5",
  });
  const savedRef = useRef<AgentDraft>({
    cleanupDelaySeconds: "300",
    maxConcurrentSessions: "5",
  });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const res = await API.getSystemConfig();
      setRemoteData(res);
      const d = buildDraft(res);
      savedRef.current = d;
      setDraft(d);
    } catch (err) {
      setLoadError(errMsg(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const isDirty = !deepEqual(draft, savedRef.current);
  useWarnUnsaved(isDirty);

  const updateDraft = useCallback(
    <K extends keyof AgentDraft>(key: K, value: AgentDraft[K]) => {
      setDraft((prev) => ({ ...prev, [key]: value }));
      setSaveError(null);
    },
    [],
  );

  const handleSave = useCallback(async () => {
    const patch = buildPatch(draft, savedRef.current);
    if (Object.keys(patch).length === 0) return;
    setSaving(true);
    setSaveError(null);
    try {
      const res = await API.updateSystemConfig(patch);
      setRemoteData(res);
      const newDraft = buildDraft(res);
      savedRef.current = newDraft;
      setDraft(newDraft);
      voidCall(useConfigStatusStore.getState().refresh());
      useAppStore.getState().pushToast(t("agent_config_saved"), "success");
    } catch (err) {
      setSaveError(errMsg(err));
    } finally {
      setSaving(false);
    }
  }, [draft, t]);

  const handleReset = useCallback(() => {
    setDraft(savedRef.current);
    setSaveError(null);
  }, []);

  if (loadError) {
    return (
      <div className={visible ? "px-1 py-8" : "hidden"}>
        <div
          role="alert"
          className="flex items-start gap-1.5 rounded-[8px] border px-4 py-3 text-[12.5px]"
          style={{
            borderColor: "var(--color-warm-ring)",
            background: "var(--color-warm-tint)",
            color: "var(--color-warm-bright)",
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
            ? "flex items-center gap-2 px-1 py-12 text-text-3"
            : "hidden"
        }
      >
        <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
        <span className="font-mono text-[11px] uppercase tracking-[0.14em]">
          {t("common:loading")}
        </span>
      </div>
    );
  }

  return (
    <div className={visible ? undefined : "hidden"}>
      <div className="space-y-7 pb-0 pt-1">
        <AgentPageIntro />
        <CredentialsSection />
        <SectionShell kicker="Runtime Tuning" title={t("advanced_settings")}>
          <div className="space-y-4">
            <div>
              <FieldLabel htmlFor="agent-cleanup-delay" className="">
                {t("session_cleanup_delay_label")}
              </FieldLabel>
              <p className="mt-0.5 text-[11.5px] text-text-4">
                {t("session_cleanup_delay_desc")}
              </p>
              <input
                id="agent-cleanup-delay"
                type="number"
                min={10}
                max={3600}
                value={draft.cleanupDelaySeconds}
                onChange={(e) => updateDraft("cleanupDelaySeconds", e.target.value)}
                className={`${INPUT_CLS} mt-1.5 max-w-[140px]`}
                disabled={saving}
              />
            </div>
            <div>
              <FieldLabel htmlFor="agent-max-sessions" className="">
                {t("max_concurrent_sessions_label")}
              </FieldLabel>
              <p className="mt-0.5 text-[11.5px] text-text-4">
                {t("max_concurrent_sessions_desc")}
              </p>
              <input
                id="agent-max-sessions"
                type="number"
                min={1}
                max={20}
                value={draft.maxConcurrentSessions}
                onChange={(e) => updateDraft("maxConcurrentSessions", e.target.value)}
                className={`${INPUT_CLS} mt-1.5 max-w-[140px]`}
                disabled={saving}
              />
            </div>
          </div>
        </SectionShell>
      </div>

      <TabSaveFooter
        isDirty={isDirty}
        saving={saving}
        disabled={false}
        error={saveError}
        onSave={() => void handleSave()}
        onReset={handleReset}
      />
    </div>
  );
}
