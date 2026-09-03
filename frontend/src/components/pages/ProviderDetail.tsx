import { useState, useEffect, useCallback, useRef, type CSSProperties } from "react";
import { errMsg, voidCall } from "@/utils/async";
import { ChevronRight, Eye, EyeOff, Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useWarnUnsaved } from "@/hooks/useWarnUnsaved";
import { API } from "@/api";
import { ProviderIcon } from "@/components/ui/ProviderIcon";
import { CredentialList } from "@/components/pages/CredentialList";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { FieldLabel } from "@/components/ui/FieldLabel";
import type { ProviderConfigDetail, ProviderField } from "@/types";

// ---------------------------------------------------------------------------
// Status badge — Darkroom OKLCH tokens
// ---------------------------------------------------------------------------

interface BadgeStyle {
  label: string;
  style: CSSProperties;
}

const STATUS_BADGE_MAP: Record<string, BadgeStyle> = {
  ready: {
    label: "status_ready",
    style: {
      background: "oklch(0.30 0.10 155 / 0.18)",
      color: "var(--color-good)",
      border: "1px solid oklch(0.45 0.10 155 / 0.40)",
      boxShadow: "0 0 14px -6px oklch(0.55 0.10 155 / 0.50)",
    },
  },
  unconfigured: {
    label: "status_unconfigured",
    style: {
      background: "var(--color-bg-grad-a)",
      color: "var(--color-text-3)",
      border: "1px solid var(--color-hairline)",
    },
  },
  error: {
    label: "status_error",
    style: {
      background: "var(--color-warm-tint)",
      color: "var(--color-warm-bright)",
      border: "1px solid var(--color-warm-ring)",
      boxShadow: "0 0 14px -6px var(--color-warm-glow)",
    },
  },
};

function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation("dashboard");
  const { label, style } = STATUS_BADGE_MAP[status] ?? STATUS_BADGE_MAP.unconfigured;
  return (
    <span
      className="rounded-full px-2.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em]"
      style={style}
    >
      {t(label)}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Field editor
// ---------------------------------------------------------------------------

interface FieldEditorProps {
  field: ProviderField;
  draft: Record<string, string>;
  setDraft: React.Dispatch<React.SetStateAction<Record<string, string>>>;
}

function FieldEditor({ field, draft, setDraft }: FieldEditorProps) {
  const { t } = useTranslation("dashboard");
  const [showSecret, setShowSecret] = useState(false);
  const [confirmingClear, setConfirmingClear] = useState(false);

  const currentValue = draft[field.key] ?? field.value ?? "";

  const handleChange = (value: string) => {
    setDraft((prev) => ({ ...prev, [field.key]: value }));
  };

  const handleClear = () => {
    if (!confirmingClear) {
      setConfirmingClear(true);
      return;
    }
    setDraft((prev) => ({ ...prev, [field.key]: "" }));
    setConfirmingClear(false);
  };

  const fieldId = `field-${field.key}`;

  if (field.type === "secret") {
    const displayValue = field.key in draft ? draft[field.key] : "";

    return (
      <div>
        <FieldLabel htmlFor={fieldId} required={field.required}>
          {field.label}
        </FieldLabel>
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <input
              id={fieldId}
              name={field.key}
              autoComplete="off"
              type={showSecret ? "text" : "password"}
              value={displayValue}
              onChange={(e) => handleChange(e.target.value)}
              placeholder={
                field.is_set
                  ? field.value_masked ?? "••••••••••"
                  : field.placeholder ?? t("enter_key_placeholder")
              }
              className={`${INPUT_CLS} pr-9`}
            />
            <button
              type="button"
              onClick={() => setShowSecret((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded text-text-4 transition-colors hover:text-text-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              aria-label={showSecret ? t("common:hide") : t("common:show")}
            >
              {showSecret ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
            </button>
          </div>
          {field.is_set && !confirmingClear && (
            <button
              type="button"
              onClick={handleClear}
              title={t("clear_key")}
              className={GHOST_BTN_CLS}
            >
              <X className="h-3 w-3" />
              {t("clear_label")}
            </button>
          )}
          {confirmingClear && (
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={handleClear}
                className="inline-flex items-center gap-1 rounded-[8px] px-3 py-1.5 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                style={{
                  background: "var(--color-warm-tint)",
                  color: "var(--color-warm-bright)",
                  border: "1px solid var(--color-warm-ring)",
                }}
              >
                {t("confirm_clear")}
              </button>
              <button
                type="button"
                onClick={() => setConfirmingClear(false)}
                className={GHOST_BTN_CLS}
              >
                {t("common:cancel")}
              </button>
            </div>
          )}
        </div>
        {field.is_set && !(field.key in draft) && (
          <p className="mt-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-text-4">
            {t("key_set_hint")}
          </p>
        )}
      </div>
    );
  }

  if (field.type === "number") {
    return (
      <div>
        <FieldLabel htmlFor={fieldId} required={field.required}>
          {field.label}
        </FieldLabel>
        <input
          id={fieldId}
          name={field.key}
          autoComplete="off"
          type="number"
          value={currentValue}
          onChange={(e) => handleChange(e.target.value)}
          placeholder={field.placeholder ?? ""}
          className={`${INPUT_CLS} max-w-[140px]`}
        />
      </div>
    );
  }

  return (
    <div>
      <FieldLabel htmlFor={fieldId} required={field.required}>
        {field.label}
      </FieldLabel>
      <input
        id={fieldId}
        name={field.key}
        autoComplete="off"
        type={field.type === "url" ? "url" : "text"}
        value={currentValue}
        onChange={(e) => handleChange(e.target.value)}
        placeholder={field.placeholder ?? ""}
        className={INPUT_CLS}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Capability pill
// ---------------------------------------------------------------------------

// 能力徽标展示顺序：图片→视频→文本→音频，未列出的类型排到末尾。
// media_types 由后端按注册顺序返回，前端统一排序避免 audio 等新类型插到队首。
const CAPABILITY_PILL_ORDER = ["image", "video", "text", "audio"];

function capabilityPillRank(kind: string): number {
  const idx = CAPABILITY_PILL_ORDER.indexOf(kind);
  return idx === -1 ? CAPABILITY_PILL_ORDER.length : idx;
}

function CapabilityPill({ kind }: { kind: string }) {
  const { t } = useTranslation("dashboard");
  const label =
    kind === "video"
      ? t("media_type_video")
      : kind === "image"
        ? t("media_type_image")
        : kind === "text"
          ? t("media_type_text")
          : kind === "audio"
            ? t("media_type_audio")
            : kind;
  return (
    <span className="rounded-full border border-hairline-soft bg-bg-grad-a/55 px-2.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-3">
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

interface Props {
  providerId: string;
  onSaved?: () => void;
}

export function ProviderDetail({ providerId, onSaved }: Props) {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  const [detail, setDetail] = useState<ProviderConfigDetail | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const loadedContextRef = useRef<{ providerId: string; reloadKey: number } | null>(null);
  // 面板重置的代次：只在换供应商或手动重试时递增，语言重取不动它。在途的保存拿它认自己
  // 那一次面板停留，A→B→A 回到同一个 providerId 时旧保存不会被认成当前的。
  const panelGenerationRef = useRef(0);
  const detailRef = useRef<ProviderConfigDetail | null>(null);
  const detailAbortRef = useRef<AbortController | null>(null);

  const applyDetail = useCallback((next: ProviderConfigDetail | null) => {
    detailRef.current = next;
    setDetail(next);
  }, []);

  /** 详情有三个发起方（effect、保存后重取、凭证变更后重取）。新一轮先作废在途的那次，
   *  否则先发出的语言重取后返回时会把刚保存的结果覆盖回旧值。 */
  const startDetailRequest = useCallback(() => {
    detailAbortRef.current?.abort();
    const controller = new AbortController();
    detailAbortRef.current = controller;
    return controller;
  }, []);

  const hasDraft = Object.keys(draft).length > 0;
  useWarnUnsaved(hasDraft);

  const handleCredentialChanged = useCallback(() => {
    // 凭证已经改完了：目录刷新与这次详情重取是否落地无关，与保存路径同一口径。
    onSaved?.();
    const controller = startDetailRequest();
    voidCall(
      API.getProviderConfig(providerId, { signal: controller.signal }).then((updated) => {
        if (!controller.signal.aborted) applyDetail(updated);
      }),
    );
  }, [providerId, onSaved, applyDetail, startDetailRequest]);

  // 用户编辑草稿时同步清掉上一次保存失败的错误，避免旧文案滞留误导
  const handleDraftEdit = useCallback<React.Dispatch<React.SetStateAction<Record<string, string>>>>((action) => {
    setSaveError(null);
    setDraft(action);
  }, []);

  useEffect(() => {
    const previous = loadedContextRef.current;
    const shouldReset =
      previous === null || previous.providerId !== providerId || previous.reloadKey !== reloadKey;
    loadedContextRef.current = { providerId, reloadKey };
    if (shouldReset) {
      panelGenerationRef.current += 1;
      // providerId 变化或手动重试时重置草稿/详情/错误；语言切换只换详情，保留未保存草稿。
      setDraft({});
      applyDetail(null);
      setLoadError(null);
      setSaveError(null);
    }
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
    return () => {
      controller.abort();
    };
  }, [i18n.language, providerId, reloadKey, applyDetail, startDetailRequest]);

  const handleSave = useCallback(async () => {
    if (Object.keys(draft).length === 0) return;
    setSaving(true);
    setSaveError(null);
    // 面板可能在 PATCH 途中切到别的供应商。这次保存之后的每一次面板状态写入都要先确认
    // 面板还停在发起保存的那一次停留上，否则会清掉别人的草稿、把本次的错误显示在别人的
    // 表单上；只比 providerId 的话，离开又切回来的面板会把旧保存重新认成当前的。
    const savedGeneration = panelGenerationRef.current;
    const stillOnThisProvider = () => panelGenerationRef.current === savedGeneration;
    let saved = false;
    try {
      const patch: Record<string, string | null> = {};
      for (const [key, value] of Object.entries(draft)) {
        patch[key] = value || null;
      }
      await API.patchProviderConfig(providerId, patch);
      saved = true;
      // 已经离场就不重取：那份详情由新供应商自己的 effect 负责，这里再取一次只会把旧
      // 供应商的字段画进新面板，还会把它的加载作废掉。
      if (stillOnThisProvider()) {
        const controller = startDetailRequest();
        const updated = await API.getProviderConfig(providerId, { signal: controller.signal });
        if (!controller.signal.aborted) applyDetail(updated);
      }
    } catch (err) {
      // 保存已成功、只是重取没落地：不是保存失败，详情停在旧值即可
      // 后端校验失败（如 Max Workers 非法值）返回已本地化的 detail，直接展示
      if (!saved && stillOnThisProvider()) setSaveError(errMsg(err));
    } finally {
      // 保存成功的收尾与重取是否落地无关：草稿必须清掉，目录必须刷新，
      // 否则已入库的值仍标着未保存、还能被重复提交。
      if (saved) {
        if (stillOnThisProvider()) setDraft({});
        // 目录刷新与面板停在谁身上无关：入库的确实是这个供应商。
        onSaved?.();
      }
      setSaving(false);
    }
  }, [draft, providerId, onSaved, applyDetail, startDetailRequest]);

  if (loadError) {
    return (
      <div role="alert" className="flex flex-col items-start gap-2.5 px-1 py-10">
        <span className="inline-flex items-center gap-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-warm">
          {t("common:load_failed")}
        </span>
        <p className="text-[12.5px] text-text-2">{loadError}</p>
        <button
          type="button"
          onClick={() => setReloadKey((k) => k + 1)}
          className="rounded-[7px] border border-hairline-soft bg-bg-grad-a/55 px-3 py-1.5 text-[12px] text-text-2 transition-colors hover:border-hairline hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {t("common:retry")}
        </button>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex items-center gap-2 px-1 py-12 text-text-3">
        <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
        <span className="font-mono text-[11px] uppercase tracking-[0.14em]">
          {t("common:loading")}
        </span>
      </div>
    );
  }

  return (
    <div className="max-w-2xl space-y-6">
      {/* Header */}
      <div className="flex items-start gap-3">
        <ProviderIcon providerId={providerId} className="mt-0.5 h-7 w-7 shrink-0" />
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <h3
              className="font-editorial"
              style={{
                fontSize: 22,
                fontWeight: 400,
                lineHeight: 1.1,
                letterSpacing: "-0.012em",
                color: "var(--color-text)",
              }}
            >
              {detail.display_name}
            </h3>
            <StatusBadge status={detail.status} />
          </div>
          {detail.description && (
            <p className="mt-1.5 text-[12.5px] leading-[1.55] text-text-3">
              {detail.description}
            </p>
          )}
        </div>
      </div>

      {/* Capabilities */}
      {detail.media_types && detail.media_types.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {[...detail.media_types]
            .sort((a, b) => capabilityPillRank(a) - capabilityPillRank(b))
            .map((mt) => (
              <CapabilityPill key={mt} kind={mt} />
            ))}
        </div>
      )}

      {/* Credentials */}
      <CredentialList
        providerId={providerId}
        supportsBaseUrl={detail.supports_base_url}
        secretFields={detail.secret_fields}
        secretFieldGroups={detail.secret_field_groups}
        onChanged={handleCredentialChanged}
      />

      {/* Advanced */}
      {detail.fields.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="inline-flex items-center gap-1 rounded font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-text-3 transition-colors hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <ChevronRight
              className={`h-3.5 w-3.5 transition-transform ${showAdvanced ? "rotate-90" : ""}`}
              aria-hidden
            />
            {t("advanced_config")}
          </button>
          {showAdvanced && (
            <div className="mt-3 space-y-4">
              {detail.fields.map((field) => (
                <FieldEditor key={field.key} field={field} draft={draft} setDraft={handleDraftEdit} />
              ))}
              {hasDraft && (
                <div className="pt-1">
                  {saveError && (
                    <p
                      aria-live="polite"
                      className="mb-2 rounded-[6px] px-2.5 py-1.5 text-[11.5px]"
                      style={{
                        background: "var(--color-warm-tint)",
                        color: "var(--color-warm-bright)",
                        border: "1px solid var(--color-warm-ring)",
                      }}
                    >
                      {saveError}
                    </p>
                  )}
                  <button
                    type="button"
                    onClick={() => void handleSave()}
                    disabled={saving}
                    className={ACCENT_BTN_CLS}
                    style={ACCENT_BUTTON_STYLE}
                  >
                    {saving ? (
                      <>
                        <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
                        {t("common:saving")}
                      </>
                    ) : (
                      t("save_provider")
                    )}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
