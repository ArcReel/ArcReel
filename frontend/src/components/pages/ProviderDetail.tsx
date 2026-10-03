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
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ProviderConfigDetail, ProviderField } from "@/types";

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

interface Props {
  providerId: string;
  /** 高级配置或密钥改动入库之后调用，供上层刷新供应商目录（二级栏的密钥数量与状态）。 */
  onSaved?: () => void;
}

/**
 * 预置供应商详情。换供应商时由上层按 `providerId` 重建整栏，组件内只处理同一供应商的语言重取与重试。
 * 密钥区的增改删与切换立即生效；底部保存栏只负责高级配置（并发上限等）。
 */
export function ProviderDetail({ providerId, onSaved }: Props) {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  const [detail, setDetail] = useState<ProviderConfigDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const detailRef = useRef<ProviderConfigDetail | null>(null);
  const detailAbortRef = useRef<AbortController | null>(null);
  const advancedHeadingId = useId();

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

  const source = useMemo(() => advancedValues(detail), [detail]);
  const saveAdvanced = useCallback(
    async (value: AdvancedValues, saved: AdvancedValues): Promise<AdvancedValues | void> => {
      const patch: Record<string, string | null> = {};
      for (const [key, next] of Object.entries(value)) {
        if (next !== saved[key]) patch[key] = next.trim() || null;
      }
      // 后端校验失败（如并发上限非法）返回已本地化的 detail，由保存栏显示
      await API.patchProviderConfig(providerId, patch);
      onSaved?.();
      // 入库值经后端规范化（如「+5」存为「5」），以重取结果为准；重取失败不算保存失败。
      try {
        const updated = await refetchDetail();
        if (updated) return advancedValues(updated);
      } catch {
        // 详情停在旧值即可
      }
    },
    [providerId, onSaved, refetchDetail],
  );
  const unit = useEditUnit({ source, save: saveAdvanced });

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

  if (!detail) {
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
      footer={detail.fields.length > 0 ? <SaveBar unit={unit} className="max-w-178" /> : undefined}
    >
      <div className="flex max-w-190 flex-col gap-8 px-6 py-6">
        <CredentialList
          providerId={providerId}
          supportsBaseUrl={detail.supports_base_url}
          secretFields={detail.secret_fields}
          secretFieldGroups={detail.secret_field_groups}
          onChanged={handleCredentialChanged}
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
                    value={unit.value[field.key] ?? ""}
                    onChange={(e) => {
                      const next = e.target.value;
                      unit.setValue((prev) => ({ ...prev, [field.key]: next }));
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
