import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { Loader2, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { PageShellFooter } from "@/components/shared/page-shell/PageShell";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useAppStore } from "@/stores/app-store";
import type { MarketSourceInfo, OfficialServiceState } from "@/types";
import { errMsg } from "@/utils/async";
import { MarketSourceList } from "./MarketSourceList";

function SettingsBlock({
  title,
  description,
  descriptionId,
  children,
}: {
  title: string;
  description?: string;
  /** 区块内的控件需要 aria-describedby 指向说明时传入。 */
  descriptionId?: string;
  children: ReactNode;
}) {
  const titleId = useId();
  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
      <div className="flex flex-col gap-1">
        <h3 id={titleId} className="text-sm font-medium">
          {title}
        </h3>
        {description && (
          <p id={descriptionId} className="text-sm text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {children}
    </section>
  );
}

/**
 * 「设置」Tab：市场源、GitHub 访问代理与官方服务。代理前缀是表单字段，经外壳底行的保存栏保存；
 * 市场源的增删、启停、排序与官方服务开关即时生效。这一区常驻，官方服务关闭后照常显示，是重新开启的入口。
 */
export function MarketSettingsTab({
  active,
  sources,
  onSourcesChange,
  refreshingIds,
  refreshingAll,
  onRefresh,
  onRefreshAll,
  official,
  officialLoadError,
  onOfficialRetry,
  officialBusy,
  onOfficialChange,
  onOfficialUpdate,
}: {
  active: boolean;
  sources: MarketSourceInfo[];
  onSourcesChange: (update: (current: MarketSourceInfo[]) => MarketSourceInfo[]) => void;
  refreshingIds: ReadonlySet<number>;
  refreshingAll: boolean;
  onRefresh: (id: number) => void;
  onRefreshAll: () => void;
  official: OfficialServiceState | null;
  officialLoadError: string | null;
  onOfficialRetry: () => void;
  officialBusy: boolean;
  onOfficialChange: (state: OfficialServiceState) => void;
  onOfficialUpdate: (patch: { enabled?: boolean }) => void;
}) {
  return (
    <div className="flex max-w-190 flex-col gap-6">
      <MarketSourceList
        sources={sources}
        onSourcesChange={onSourcesChange}
        refreshingIds={refreshingIds}
        refreshingAll={refreshingAll}
        onRefresh={onRefresh}
        onRefreshAll={onRefreshAll}
      />
      <GithubProxySettings active={active} />
      {official ? (
        <OfficialServiceSettings
          state={official}
          busy={officialBusy}
          onChange={onOfficialChange}
          onUpdate={onOfficialUpdate}
        />
      ) : (
        officialLoadError && <OfficialServiceLoadError message={officialLoadError} onRetry={onOfficialRetry} />
      )}
    </div>
  );
}

/** 官方服务状态读取失败：保留区块与重试，否则「设置」里没有重新开启的入口。 */
function OfficialServiceLoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  const { t } = useTranslation(["dashboard", "common"]);
  return (
    <SettingsBlock title={t("official_service_title")}>
      <LoadErrorRow message={message} onRetry={onRetry} />
    </SettingsBlock>
  );
}

function LoadErrorRow({ message, onRetry }: { message: string; onRetry: () => void }) {
  const { t } = useTranslation("common");
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm text-destructive" role="alert">
      {message}
      <Button variant="outline" size="sm" onClick={onRetry}>
        {t("retry")}
      </Button>
    </div>
  );
}

interface ProxyFields {
  market_github_proxy_prefix: string;
}

function GithubProxySettings({ active }: { active: boolean }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const inputId = useId();
  const hintId = useId();
  const [saved, setSaved] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadController = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    loadController.current?.abort();
    const controller = new AbortController();
    loadController.current = controller;
    setLoadError(null);
    try {
      const { settings } = await API.getSystemConfig({ signal: controller.signal });
      if (controller.signal.aborted) return;
      setSaved(settings.market_github_proxy_prefix ?? "");
    } catch (err) {
      if (controller.signal.aborted) return;
      setLoadError(errMsg(err));
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount 后异步拉取配置，回调内回写状态
    void load();
    return () => loadController.current?.abort();
  }, [load]);

  const source = useMemo<ProxyFields>(() => ({ market_github_proxy_prefix: saved ?? "" }), [saved]);
  const save = useCallback(async (value: ProxyFields) => {
    const { settings } = await API.updateSystemConfig({ market_github_proxy_prefix: value.market_github_proxy_prefix });
    const next = settings.market_github_proxy_prefix ?? "";
    setSaved(next);
    return { market_github_proxy_prefix: next };
  }, []);
  const unit = useEditUnit({ source, save });

  return (
    <SettingsBlock title={t("market_proxy_label")}>
      {loadError ? (
        <LoadErrorRow message={loadError} onRetry={() => void load()} />
      ) : saved === null ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          {t("common:loading")}
        </p>
      ) : (
        <>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={inputId}>{t("market_proxy_field_label")}</Label>
            <Input
              id={inputId}
              type="url"
              aria-describedby={hintId}
              placeholder="https://proxy.example.com/"
              value={unit.value.market_github_proxy_prefix}
              onChange={(event) => unit.setValue({ market_github_proxy_prefix: event.target.value })}
            />
            <p id={hintId} className="text-sm text-muted-foreground">
              {t("market_proxy_hint")}
            </p>
          </div>
          {active && (
            <PageShellFooter constrained>
              <SaveBar unit={unit} />
            </PageShellFooter>
          )}
        </>
      )}
    </SettingsBlock>
  );
}

function OfficialServiceSettings({
  state,
  busy,
  onChange,
  onUpdate,
}: {
  state: OfficialServiceState;
  busy: boolean;
  onChange: (state: OfficialServiceState) => void;
  onUpdate: (patch: { enabled?: boolean }) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const pushToast = useAppStore((s) => s.pushToast);
  const descId = useId();
  const [confirmingReset, setConfirmingReset] = useState(false);
  const [resetting, setResetting] = useState(false);

  const reset = async () => {
    setResetting(true);
    try {
      onChange(await API.resetOfficialInstanceId());
      setConfirmingReset(false);
    } catch (err) {
      pushToast(t("official_service_update_failed", { message: errMsg(err) }), "error");
    } finally {
      setResetting(false);
    }
  };

  return (
    <SettingsBlock
      title={t("official_service_title")}
      description={state.available ? t("official_service_desc") : t("official_service_not_configured")}
      descriptionId={descId}
    >
      {state.available && (
        <>
          <Label>
            <Switch
              checked={state.enabled}
              disabled={busy}
              aria-describedby={descId}
              onCheckedChange={(enabled) => onUpdate({ enabled })}
            />
            {t("official_service_toggle")}
          </Label>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
            <span className="text-muted-foreground">{t("official_service_instance_id")}</span>
            <span className="min-w-0 font-mono break-all text-subtle-foreground">
              {state.instance_id ?? t("official_service_instance_id_none")}
            </span>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirmingReset(true)}>
              <RotateCcw data-icon="inline-start" aria-hidden />
              {t("official_service_reset")}
            </Button>
          </div>
        </>
      )}
      <AlertDialog
        open={confirmingReset}
        onOpenChange={(next) => {
          if (!next && !resetting) setConfirmingReset(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("official_service_reset_title")}</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogBody tabIndex={0} role="region" aria-label={t("official_service_reset_title")}>
            <AlertDialogDescription>{t("official_service_reset_desc")}</AlertDialogDescription>
          </AlertDialogBody>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={resetting}>{t("common:cancel")}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" disabled={resetting} onClick={() => void reset()}>
              {resetting && <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />}
              {t("official_service_reset_confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SettingsBlock>
  );
}
