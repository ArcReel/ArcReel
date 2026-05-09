import { useState, useEffect, useCallback, type CSSProperties } from "react";
import { voidCall, voidPromise } from "@/utils/async";
import { ChevronRight, Eye, EyeOff, Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useWarnUnsaved } from "@/hooks/useWarnUnsaved";
import { API } from "@/api";
import { ProviderIcon } from "@/components/ui/ProviderIcon";
import { CredentialList } from "@/components/pages/CredentialList";
import type { ProviderConfigDetail, ProviderField } from "@/types";

const ACCENT_BUTTON_STYLE: CSSProperties = {
  color: "oklch(0.14 0 0)",
  background: "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
  boxShadow:
    "inset 0 1px 0 oklch(1 0 0 / 0.3), 0 0 0 1px oklch(0.55 0.10 295 / 0.4), 0 6px 18px -8px var(--color-accent-glow)",
};

const INPUT_CLS =
  "w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[13px] text-text placeholder:text-text-4 transition-colors hover:border-hairline-strong focus:border-accent/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

const GHOST_BTN_CLS =
  "inline-flex items-center gap-1.5 rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-1.5 text-[12px] text-text-2 transition-colors hover:border-hairline-strong hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50";

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

function FieldLabel({
  htmlFor,
  required,
  children,
}: {
  htmlFor: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label
      htmlFor={htmlFor}
      className="mb-1.5 block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-2"
    >
      {children}
      {required && <span className="ml-1 text-warm-bright">*</span>}
    </label>
  );
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
  const { t } = useTranslation("dashboard");
  const [detail, setDetail] = useState<ProviderConfigDetail | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const hasDraft = Object.keys(draft).length > 0;
  useWarnUnsaved(hasDraft);

  const handleCredentialChanged = useCallback(async () => {
    const updated = await API.getProviderConfig(providerId);
    setDetail(updated);
    onSaved?.();
  }, [providerId, onSaved]);

  useEffect(() => {
    let disposed = false;
    setDraft({});
    setDetail(null);
    voidCall(
      API.getProviderConfig(providerId).then((res) => {
        if (!disposed) setDetail(res);
      }),
    );
    return () => {
      disposed = true;
    };
  }, [providerId]);

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
                    className="inline-flex items-center gap-2 rounded-[8px] px-4 py-2 text-[12.5px] font-semibold transition-transform motion-safe:hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50"
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
