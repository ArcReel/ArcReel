import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { API } from "@/api";
import { errMsg, voidCall } from "@/utils/async";
import { ProviderIcon } from "@/components/shared/ProviderIcon";
import { CredentialList } from "@/components/pages/CredentialList";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { PartialSaveError, useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { FieldHint } from "@/components/pages/settings/CustomProviderFieldRow";
import { ProviderModelList } from "@/components/pages/settings/ProviderModelList";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ProviderConfigDetail, ProviderField, ProviderMultiplierInfo } from "@/types";
import { parseMultiplier } from "@/utils/multiplier";

// 能力徽标展示顺序：图片→视频→文本→音频，未列出的类型排到末尾。
// media_types 由后端按注册顺序返回，前端统一排序避免 audio 等新类型插到队首。
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

// 高级配置字段的界面名：后端只给英文 label，已知字段走本地化文案，未知字段回退后端 label。
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

/** 高级配置的已保存内容：字段 key → 当前值，未设置为空串。 */
type AdvancedValues = Record<string, string>;

function advancedValues(detail: ProviderConfigDetail | null): AdvancedValues {
  return Object.fromEntries((detail?.fields ?? []).map((field) => [field.key, field.value ?? ""]));
}

// 订阅制供应商：倍率默认 0，不计算费用。
const SUBSCRIPTION_PROVIDERS = new Set(["ark-agent-plan"]);

/** 供应商倍率的输入值：等于默认值时为空串，表示跟随默认；保存空串即恢复默认。 */
function multiplierText(info: ProviderMultiplierInfo | null): string {
  if (!info || info.multiplier === info.default) return "";
  return String(info.multiplier);
}

/** 保存栏管理的内容：顶部的供应商倍率与高级配置。 */
interface DetailValues {
  multiplier: string;
  advanced: AdvancedValues;
}

function isMultiplierValid(text: string): boolean {
  return !text.trim() || parseMultiplier(text) !== undefined;
}

interface Props {
  providerId: string;
  /** 高级配置或密钥改动入库之后调用，供上层刷新供应商目录（二级栏的密钥数量与状态）。 */
  onSaved?: () => void;
}

/**
 * 预置供应商详情。换供应商时由上层按 `providerId` 重建整栏，组件内只处理同一供应商的语言重取与重试。
 * 密钥区的增改删与切换、模型列表的启用开关立即生效；底部保存栏负责供应商倍率与高级配置（并发上限等）。
 */
export function ProviderDetail({ providerId, onSaved }: Props) {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  const [detail, setDetail] = useState<ProviderConfigDetail | null>(null);
  const [multiplier, setMultiplier] = useState<ProviderMultiplierInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
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

  /** 详情有三个发起方（effect、保存后重取、密钥变更后重取）。新一轮先作废在途的那次，
   *  否则先发出的语言重取后返回时会把刚保存的结果覆盖回旧值。 */
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
          setLoadError(null);
        })
        .catch((err: unknown) => {
          // 语言重取失败时旧详情仍可读，静默即可；手上没有详情可展示就必须报错，
          // 否则页面停在加载态且没有重试入口。
          if (!controller.signal.aborted && detailRef.current === null) setLoadError(errMsg(err));
        }),
    );
    return () => controller.abort();
  }, [i18n.language, providerId, reloadKey, applyDetail, startDetailRequest]);

  // 倍率与语言无关，只随供应商取一次；取齐之前不渲染保存栏，后到的倍率不会被当成外部更新覆盖编辑。
  useEffect(() => {
    const controller = new AbortController();
    voidCall(
      API.getProviderMultiplier(providerId, { signal: controller.signal })
        .then((res) => {
          if (!controller.signal.aborted) setMultiplier(res);
        })
        .catch((err: unknown) => {
          if (!controller.signal.aborted) setLoadError(errMsg(err));
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
    // 密钥已经改完了：目录刷新与这次详情重取是否落地无关。
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
        // 后端校验失败（如并发上限非法）返回已本地化的 detail，由保存栏显示
        await API.patchProviderConfig(providerId, patch);
        onSaved?.();
        // 入库值经后端规范化（如「+5」存为「5」），以重取结果为准；重取失败不算保存失败。
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
        // 高级配置已经落库，按部分保存报告
        throw new PartialSaveError(t("pm_multiplier_save_failed", { message: errMsg(err) }), {
          saved: { multiplier: saved.multiplier, advanced },
          cause: err,
        });
      }
    },
    [providerId, onSaved, refetchDetail, t],
  );
  const unit = useEditUnit({ source, save });

  if (loadError) {
    return (
      <DetailPane>
        <div role="alert" className="flex flex-col items-start gap-3 p-6">
          <p className="text-sm font-medium text-warn">{t("common:load_failed")}</p>
          <p className="text-sm text-subtle-foreground">{loadError}</p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setLoadError(null);
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
      <DetailPane>
        <p className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
          {t("common:loading")}
        </p>
      </DetailPane>
    );
  }

  const mediaTypes = [...(detail.media_types ?? [])].sort((a, b) => mediaTypeRank(a) - mediaTypeRank(b));
  const multiplierValid = isMultiplierValid(unit.value.multiplier);

  return (
    <DetailPane
      header={
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
      }
      // 保存栏与正文的表单列同宽同起点（正文 max-w-190 含左右各 24px 内边距），宽窗口下不横跨整栏
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

        <ProviderModelList providerId={providerId} />

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
