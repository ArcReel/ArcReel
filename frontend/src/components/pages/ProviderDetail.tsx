import { useState, useEffect, useCallback, type CSSProperties } from "react";
import { errMsg, voidCall, voidPromise } from "@/utils/async";
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

function CapabilityPill({ kind }: { kind: string }) {
  const { t } = useTranslation("dashboard");
  const label =
    kind === "video"
      ? t("media_type_video")
      : kind === "image"
        ? t("media_type_image")
        : kind === "text"
          ? t("media_type_text")
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
  const { t } = useTranslation(["dashboard", "common"]);
  const [detail, setDetail] = useState<ProviderConfigDetail | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const hasDraft = Object.keys(draft).length > 0;
  useWarnUnsaved(hasDraft);

  const handleCredentialChanged = useCallback(async () => {
    const updated = await API.getProviderConfig(providerId);
    setDetail(updated);
    onSaved?.();
  }, [providerId, onSaved]);

  useEffect(() => {
    let disposed = false;
    // providerId 变化时重置草稿/详情/错误后再异步拉取，属于动作驱动重置
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDraft({});
    setDetail(null);
    setLoadError(null);
    voidCall(
      API.getProviderConfig(providerId)
        .then((res) => {
          if (!disposed) setDetail(res);
        })
        .catch((err: unknown) => {
          if (!disposed) setLoadError(errMsg(err));
        }),
    );
    return () => {
      disposed = true;
    };
  }, [providerId, reloadKey]);

  const handleSave = useCallback(async () => {
    if (Object.keys(draft).length === 0) return;
    setSaving(true);
    try {
      const patch: Record<string, string | null> = {};
      for (const [key, value] of Object.entries(draft)) {
        patch[key] = value || null;
      }
      await API.patchProviderConfig(providerId, patch);
      const updated = await API.getProviderConfig(providerId);
      setDetail(updated);
      setDraft({});
      onSaved?.();
    } finally {
      setSaving(false);
    }
  }, [draft, providerId, onSaved]);

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
          {detail.media_types.map((mt) => (
            <CapabilityPill key={mt} kind={mt} />
          ))}
        </div>
      )}

      {/* Credentials */}
      <CredentialList providerId={providerId} onChanged={voidPromise(handleCredentialChanged)} />

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
                <FieldEditor key={field.key} field={field} draft={draft} setDraft={setDraft} />
              ))}
              {hasDraft && (
                <div className="pt-1">
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
