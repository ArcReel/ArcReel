import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { API } from "@/api";
import { errMsg, voidCall } from "@/utils/async";
import { ProviderIcon } from "@/components/shared/ProviderIcon";
import { ProviderPolicyNotice } from "@/components/shared/ProviderPolicyNotice";
import { PROVIDER_POLICY_NOTICES } from "@/components/shared/providerPolicyNotices";
import { CredentialList } from "@/components/pages/CredentialList";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { PartialSaveError, useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { FieldHint } from "@/components/pages/settings/CustomProviderFieldRow";
import { ProviderModelList } from "@/components/pages/settings/ProviderModelList";
import type { ProviderChannelOption } from "@/components/pages/settings/providerGroups";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { ProviderConfigDetail, ProviderField, ProviderMultiplierInfo } from "@/types";
import { parseMultiplier } from "@/utils/multiplier";

const MEDIA_TYPE_ORDER = ["image", "video", "text", "audio"];
const MEDIA_TYPE_LABEL_KEY: Record<string, string> = {
  image: "media_type_image",
  video: "media_type_video",
  text: "media_type_text",
  audio: "media_type_audio",
};

function mediaTypeRank(kind: string): number {
  const idx = MEDIA_TYPE_ORDER.indexOf(kind);
  return idx === -1 ? MEDIA_TYPE_ORDER.length : idx;
}

const STATUS_LABEL_KEY: Record<ProviderConfigDetail["status"], string> = {
  ready: "status_ready",
  unconfigured: "status_unconfigured",
  error: "status_error",
};

const FIELD_LABEL_KEY: Record<string, string> = {
  image_max_workers: "cp_image_max_workers_label",
  video_max_workers: "cp_video_max_workers_label",
  audio_max_workers: "cp_audio_max_workers_label",
  image_rpm: "provider_field_image_rpm",
  video_rpm: "provider_field_video_rpm",
  request_gap: "provider_field_request_gap",
  gcs_bucket: "provider_field_gcs_bucket",
  wan3_base_url: "provider_field_wan3_base_url",
};

function fieldLabel(t: TFunction, field: ProviderField): string {
  const key = FIELD_LABEL_KEY[field.key];
  return key ? t(key) : field.label;
}

function fieldPlaceholder(t: TFunction, field: ProviderField): string | undefined {
  if (field.type === "number") return t("cp_max_workers_placeholder");
  if (field.type === "url") return t("default_url_placeholder");
  return field.placeholder;
}

type AdvancedValues = Record<string, string>;

function advancedValues(detail: ProviderConfigDetail | null): AdvancedValues {
  return Object.fromEntries((detail?.fields ?? []).map((field) => [field.key, field.value ?? ""]));
}

const SUBSCRIPTION_PROVIDERS = new Set(["ark-agent-plan"]);

function multiplierText(info: ProviderMultiplierInfo | null): string {
  if (!info || info.multiplier === info.default) return "";
  return String(info.multiplier);
}

interface DetailValues {
  multiplier: string;
  advanced: AdvancedValues;
}

function isMultiplierValid(text: string): boolean {
  return !text.trim() || parseMultiplier(text) !== undefined;
}

interface ChannelTabsProps {
  providerId: string;
  channels?: ProviderChannelOption[];
  onSelectChannel?: (providerId: string) => void;
}

function ProviderChannelTabs({ providerId, channels, onSelectChannel }: ChannelTabsProps) {
  const { t } = useTranslation("dashboard");
  if (!channels || channels.length <= 1 || !onSelectChannel) return null;
  return (
    <div className="relative flex min-w-0 overflow-x-auto scroll-fade-x">
      <Tabs value={providerId} onValueChange={(nextId: string) => onSelectChannel(nextId)} className="shrink-0">
        <TabsList aria-label={t("provider_channels_aria")}>
          {channels.map((channel) => (
            <TabsTrigger key={channel.providerId} value={channel.providerId}>
              {t(channel.tabLabelKey)}
              {channel.credentialCount > 0 && (
                <>
                  <span aria-hidden className="size-1.5 rounded-full bg-good" />
                  <span className="sr-only">
                    {" "}
                    {t("provider_credential_count", { count: channel.credentialCount })}
                  </span>
                </>
              )}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
    </div>
  );
}

function ProviderDetailHeader({
  providerId,
  detail,
  channels,
  onSelectChannel,
}: {
  providerId: string;
  detail: ProviderConfigDetail;
  channels?: ProviderChannelOption[];
  onSelectChannel?: (providerId: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  const mediaTypes = [...(detail.media_types ?? [])].sort((a, b) => mediaTypeRank(a) - mediaTypeRank(b));
  const policyNotice = PROVIDER_POLICY_NOTICES[providerId];
  return (
    <div className="flex flex-col gap-3">
      <ProviderChannelTabs providerId={providerId} channels={channels} onSelectChannel={onSelectChannel} />
      <div className="flex items-start gap-3">
        <ProviderIcon providerId={providerId} className="mt-0.5 size-7 shrink-0" />
        <div className="flex min-w-0 flex-col gap-1.5">
          <div className="flex min-w-0 items-center gap-2">
            <h2 className="text-lg font-medium">{detail.display_name}</h2>
            <Badge variant={detail.status === "ready" ? "secondary" : "outline"}>
              {t(STATUS_LABEL_KEY[detail.status])}
            </Badge>
          </div>
          {detail.description && (
            <p className="max-w-[40em] text-sm text-muted-foreground">{detail.description}</p>
          )}
          {policyNotice && (
            <ProviderPolicyNotice notice={policyNotice} className="max-w-[40em] text-xs text-muted-foreground" />
          )}
          {mediaTypes.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {mediaTypes.map((kind) => (
                <Badge key={kind} variant="outline">
                  {MEDIA_TYPE_LABEL_KEY[kind] ? t(MEDIA_TYPE_LABEL_KEY[kind]) : kind}
                </Badge>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

interface Props {
  providerId: string;
  initialModelId?: string;
  channels?: ProviderChannelOption[];
  onSelectChannel?: (providerId: string) => void;
  onSaved?: () => void;
}

export function ProviderDetail({ providerId, initialModelId, channels, onSelectChannel, onSaved }: Props) {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  const [detail, setDetail] = useState<ProviderConfigDetail | null>(null);
  const [multiplier, setMultiplier] = useState<ProviderMultiplierInfo | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [multiplierError, setMultiplierError] = useState<string | null>(null);
  const loadError = detailError ?? multiplierError;
  const [reloadKey, setReloadKey] = useState(0);
  const detailRef = useRef<ProviderConfigDetail | null>(null);
  const detailAbortRef = useRef<AbortController | null>(null);
  const advancedHeadingId = useId();
  const billingHeadingId = useId();
  const multiplierInputId = useId();

  const applyDetail = useCallback((next: ProviderConfigDetail) => {
    detailRef.current = next;
    setDetail(next);
  }, []);

  const startDetailRequest = useCallback(() => {
    detailAbortRef.current?.abort();
    const controller = new AbortController();
    detailAbortRef.current = controller;
    return controller;
  }, []);

  useEffect(() => {
    const controller = startDetailRequest();
    voidCall(
      API.getProviderConfig(providerId, { signal: controller.signal })
        .then((res) => {
          if (controller.signal.aborted) return;
          applyDetail(res);
          setDetailError(null);
        })
        .catch((err: unknown) => {
          if (!controller.signal.aborted && detailRef.current === null) setDetailError(errMsg(err));
        }),
    );
    return () => controller.abort();
  }, [i18n.language, providerId, reloadKey, applyDetail, startDetailRequest]);

  useEffect(() => {
    const controller = new AbortController();
    voidCall(
      API.getProviderMultiplier(providerId, { signal: controller.signal })
        .then((res) => {
          if (!controller.signal.aborted) setMultiplier(res);
        })
        .catch((err: unknown) => {
          if (!controller.signal.aborted) setMultiplierError(errMsg(err));
        }),
    );
    return () => controller.abort();
  }, [providerId, reloadKey]);

  const refetchDetail = useCallback(async () => {
    const controller = startDetailRequest();
    const updated = await API.getProviderConfig(providerId, { signal: controller.signal });
    if (controller.signal.aborted) return null;
    applyDetail(updated);
    return updated;
  }, [providerId, applyDetail, startDetailRequest]);

  const handleCredentialChanged = useCallback(() => {
    onSaved?.();
    voidCall(refetchDetail().catch(() => undefined));
  }, [onSaved, refetchDetail]);

  const source = useMemo<DetailValues>(
    () => ({ multiplier: multiplierText(multiplier), advanced: advancedValues(detail) }),
    [multiplier, detail],
  );
  const save = useCallback(
    async (value: DetailValues, saved: DetailValues): Promise<DetailValues | void> => {
      if (!isMultiplierValid(value.multiplier)) throw new Error(t("cp_multiplier_invalid"));
      const patch: Record<string, string | null> = {};
      for (const [key, next] of Object.entries(value.advanced)) {
        if (next !== saved.advanced[key]) patch[key] = next.trim() || null;
      }
      let advanced = value.advanced;
      if (Object.keys(patch).length > 0) {
        await API.patchProviderConfig(providerId, patch);
        onSaved?.();
        try {
          const updated = await refetchDetail();
          if (updated) advanced = advancedValues(updated);
        } catch {
          // 详情停在旧值即可
        }
      }
      if (value.multiplier === saved.multiplier) return { multiplier: value.multiplier, advanced };
      try {
        const info = await API.setProviderMultiplier(providerId, parseMultiplier(value.multiplier) ?? null);
        setMultiplier(info);
        return { multiplier: multiplierText(info), advanced };
      } catch (err) {
        if (Object.keys(patch).length === 0) throw err;
        throw new PartialSaveError(t("pm_multiplier_save_failed", { message: errMsg(err) }), {
          saved: { multiplier: saved.multiplier, advanced },
          cause: err,
        });
      }
    },
    [providerId, onSaved, refetchDetail, t],
  );
  const unit = useEditUnit({ source, save });

  const channelSwitcher = (
    <ProviderChannelTabs providerId={providerId} channels={channels} onSelectChannel={onSelectChannel} />
  );
  const hasChannelSwitcher = Boolean(channels && channels.length > 1 && onSelectChannel);

  if (loadError) {
    return (
      <DetailPane header={hasChannelSwitcher ? channelSwitcher : undefined}>
        <div role="alert" className="flex flex-col items-start gap-3 p-6">
          <p className="text-sm font-medium text-warn">{t("common:load_failed")}</p>
          <p className="text-sm text-subtle-foreground">{loadError}</p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setDetailError(null);
              setMultiplierError(null);
              setReloadKey((k) => k + 1);
            }}
          >
            {t("common:retry")}
          </Button>
        </div>
      </DetailPane>
    );
  }

  if (!detail || !multiplier) {
    return (
      <DetailPane header={hasChannelSwitcher ? channelSwitcher : undefined}>
        <p className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
          {t("common:loading")}
        </p>
      </DetailPane>
    );
  }

  const multiplierValid = isMultiplierValid(unit.value.multiplier);

  return (
    <DetailPane
      header={
        <ProviderDetailHeader
          providerId={providerId}
          detail={detail}
          channels={channels}
          onSelectChannel={onSelectChannel}
        />
      }
      footer={<SaveBar unit={unit} className="max-w-178" />}
    >
      <div className="flex max-w-190 flex-col gap-8 px-6 py-6">
        <CredentialList
          providerId={providerId}
          supportsBaseUrl={detail.supports_base_url}
          secretFields={detail.secret_fields}
          secretFieldGroups={detail.secret_field_groups}
          onChanged={handleCredentialChanged}
        />

        <section aria-labelledby={billingHeadingId} className="flex flex-col gap-1.5">
          <h3 id={billingHeadingId} className="sr-only">
            {t("cp_billing_heading")}
          </h3>
          <label htmlFor={multiplierInputId} className="text-sm font-medium">
            {t("pm_multiplier_label")}
          </label>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <Input
              id={multiplierInputId}
              inputMode="decimal"
              autoComplete="off"
              className="w-24"
              value={unit.value.multiplier}
              onChange={(e) => {
                const next = e.target.value;
                unit.setValue((prev) => ({ ...prev, multiplier: next }));
              }}
              placeholder={t("cp_provider_multiplier_placeholder", { value: multiplier.default })}
              aria-invalid={!multiplierValid}
              aria-describedby={`${multiplierInputId}-help`}
            />
            <p id={`${multiplierInputId}-help`} className="text-sm text-muted-foreground">
              {SUBSCRIPTION_PROVIDERS.has(providerId) ? t("pm_multiplier_help_subscription") : t("pm_multiplier_help")}
            </p>
          </div>
          {!multiplierValid && <FieldHint tone="warn">{t("cp_multiplier_invalid")}</FieldHint>}
        </section>

        <ProviderModelList
          providerId={providerId}
          providerName={detail.display_name}
          initialModelId={initialModelId}
          onModelsChanged={onSaved}
          providerMultiplier={multiplier.multiplier}
          mediaTypes={detail.media_types}
        />

        {detail.fields.length > 0 && (
          <section aria-labelledby={advancedHeadingId} className="flex flex-col gap-3">
            <div className="flex flex-col gap-1">
              <h3 id={advancedHeadingId} className="text-base font-medium">
                {t("advanced_config")}
              </h3>
              <p className="text-sm text-muted-foreground">{t("advanced_config_hint")}</p>
            </div>
            {detail.fields.map((field) => {
              const id = `provider-field-${field.key}`;
              return (
                <div key={field.key} className="flex flex-col gap-1.5">
                  <label htmlFor={id} className="text-sm font-medium">
                    {fieldLabel(t, field)}
                  </label>
                  <Input
                    id={id}
                    name={field.key}
                    autoComplete="off"
                    type={field.type === "number" ? "number" : field.type === "url" ? "url" : "text"}
                    className={field.type === "number" ? "w-32" : undefined}
                    value={unit.value.advanced[field.key] ?? ""}
                    onChange={(e) => {
                      const next = e.target.value;
                      unit.setValue((prev) => ({ ...prev, advanced: { ...prev.advanced, [field.key]: next } }));
                    }}
                    placeholder={fieldPlaceholder(t, field)}
                  />
                </div>
              );
            })}
          </section>
        )}
      </div>
    </DetailPane>
  );
}
