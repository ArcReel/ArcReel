import { ExternalLink, Star, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ACCENT_BTN_CLS,
  ACCENT_BUTTON_STYLE,
  DROPDOWN_PANEL_STYLE,
  GHOST_BTN_CLS,
  INPUT_CLS,
} from "@/components/ui/darkroom-tokens";
import { ModelCombobox } from "@/components/ui/ModelCombobox";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import type {
  CreateAgentCredentialRequest,
  PresetProvider,
} from "@/types/agent-credential";

import { PresetIcon } from "./PresetIcon";

interface Props {
  open: boolean;
  /** "create" (default) renders the new-credential form; "edit" locks the preset chips
   * and lets the user leave api_key empty to preserve the existing one. */
  mode?: "create" | "edit";
  presets: PresetProvider[];
  customSentinelId: string;
  initial?: Partial<CreateAgentCredentialRequest>;
  onSubmit: (req: CreateAgentCredentialRequest) => Promise<void>;
  onClose: () => void;
}

export function AddCredentialModal({
  open,
  mode = "create",
  presets,
  customSentinelId,
  initial,
  onSubmit,
  onClose,
}: Props) {
  const { t } = useTranslation("dashboard");
  const panelRef = useRef<HTMLDivElement>(null);
  useFocusTrap(panelRef, open);
  const [presetId, setPresetId] = useState<string>(
    initial?.preset_id ?? customSentinelId,
  );
  const [apiKey, setApiKey] = useState<string>(initial?.api_key ?? "");
  const [baseUrl, setBaseUrl] = useState<string>(initial?.base_url ?? "");
  const [displayName, setDisplayName] = useState<string>(
    initial?.display_name ?? "",
  );
  const [model, setModel] = useState<string>(initial?.model ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const selected: PresetProvider | null = useMemo(() => {
    if (presetId === customSentinelId) return null;
    return presets.find((p) => p.id === presetId) ?? null;
  }, [presetId, presets, customSentinelId]);

  // 切换预设时填默认值（仅在字段为空时同步，不覆盖用户已改的字段）
  useEffect(() => {
    if (selected) {
      setDisplayName((cur) => cur || selected.display_name);
      setModel((cur) => cur || selected.default_model);
    }
  }, [selected]);

  // 打开 modal 时按 initial 重置（edit 模式切换不同凭证 / create 复用）
  useEffect(() => {
    if (!open) return;
    setPresetId(initial?.preset_id ?? customSentinelId);
    setApiKey(initial?.api_key ?? "");
    setBaseUrl(initial?.base_url ?? "");
    setDisplayName(initial?.display_name ?? "");
    setModel(initial?.model ?? "");
    setSubmitError(null);
    // 仅在 open 状态切换或 initial 引用变化时同步；customSentinelId 几乎是稳定常量
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initial]);

  const reset = () => {
    setPresetId(customSentinelId);
    setApiKey("");
    setBaseUrl("");
    setDisplayName("");
    setModel("");
    setSubmitError(null);
  };

  // Esc 关闭
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const handleSubmit = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const req: CreateAgentCredentialRequest = {
        preset_id: presetId,
        // edit 模式留空表示保持原值；提交时仍带空字符串，由调用方决定是否透传
        api_key: apiKey,
        display_name: displayName || undefined,
        base_url: presetId === customSentinelId ? baseUrl : undefined,
        model: model || undefined,
      };
      await onSubmit(req);
      reset();
      onClose();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center px-4">
      <button
        type="button"
        aria-label="close-overlay"
        tabIndex={-1}
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-black/50"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="cred-modal-title"
        className="relative max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-[12px] border border-hairline p-5"
        style={DROPDOWN_PANEL_STYLE}
      >
        {/* Header */}
        <div className="mb-4 flex items-start justify-between">
          <h3
            id="cred-modal-title"
            className="text-[15px] font-medium text-text"
          >
            {mode === "edit" ? t("edit_credential_title") : t("add_credential")}
          </h3>
          <button
            onClick={onClose}
            className="text-text-3 hover:text-text"
            aria-label="close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Tab — unified 暂未上线，固定 claude 选中 */}
        <div className="mb-4 flex gap-1 rounded-[8px] border border-hairline p-1">
          <button
            type="button"
            className="flex-1 rounded-[6px] bg-accent py-1.5 text-[12px] font-medium text-white"
          >
            {t("claude_compat_providers")}
          </button>
          <button
            type="button"
            disabled
            className="flex-1 rounded-[6px] py-1.5 text-[12px] font-medium text-text-4"
            title={t("unified_providers_coming_soon")}
          >
            {t("unified_providers_coming_soon")}
          </button>
        </div>

        {/* Preset grid — 3 列固定网格，custom 固定首格，推荐项前置 */}
        <div className="mb-5">
          <div className="mb-2 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-text-2">
            {t("select_provider")}
          </div>
          <div className="grid grid-cols-3 gap-1.5">
            <PresetChip
              dataTestid="preset-chip"
              selected={presetId === customSentinelId}
              onClick={() => setPresetId(customSentinelId)}
              label={t("custom_config")}
              disabled={mode === "edit"}
              title={mode === "edit" ? t("preset_locked_in_edit") : undefined}
            />
            {[...presets]
              .sort(
                (a, b) =>
                  Number(b.is_recommended) - Number(a.is_recommended),
              )
              .map((p) => (
                <PresetChip
                  key={p.id}
                  dataTestid="preset-chip"
                  selected={presetId === p.id}
                  onClick={() => setPresetId(p.id)}
                  label={p.display_name}
                  iconKey={p.icon_key}
                  recommended={p.is_recommended}
                  disabled={mode === "edit"}
                  title={mode === "edit" ? t("preset_locked_in_edit") : undefined}
                />
              ))}
          </div>
        </div>

        {/* Form */}
        <div className="space-y-4">
          <Field label={t("display_name")} htmlFor="cred-name">
            <input
              id="cred-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className={INPUT_CLS}
            />
          </Field>

          {presetId === customSentinelId && (
            <Field label={t("api_base_url")} htmlFor="cred-url">
              <input
                id="cred-url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://api.example.com/anthropic"
                className={INPUT_CLS}
              />
            </Field>
          )}

          <Field
            label={t("anthropic_api_key")}
            htmlFor="cred-key"
            trailing={
              selected?.api_key_url ? (
                <a
                  href={selected.api_key_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
                >
                  {t("get_api_key")}
                  <ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              ) : null
            }
          >
            <input
              id="cred-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              autoComplete="off"
              placeholder={mode === "edit" ? t("api_key_unchanged_hint") : undefined}
              className={INPUT_CLS}
            />
          </Field>

          <Field label={t("default_model")} htmlFor="cred-model">
            <ModelCombobox
              id="cred-model"
              value={model}
              onChange={setModel}
              options={selected?.suggested_models ?? []}
              placeholder={selected?.default_model ?? "claude-sonnet-4"}
              clearable
            />
          </Field>

          {selected?.notes && (
            <div className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/45 px-3 py-2 text-[11.5px] text-text-3">
              {selected.notes}
            </div>
          )}

          {submitError && (
            <div className="text-[11.5px] text-warm-bright">{submitError}</div>
          )}
        </div>

        {/* Footer */}
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className={GHOST_BTN_CLS}>
            {t("common:cancel")}
          </button>
          <button
            onClick={() => void handleSubmit()}
            disabled={
              submitting ||
              // create 模式必须填 api_key；edit 模式留空表示保持原值
              (mode === "create" && !apiKey.trim()) ||
              (presetId === customSentinelId && !baseUrl.trim())
            }
            className={ACCENT_BTN_CLS}
            style={ACCENT_BUTTON_STYLE}
          >
            {submitting
              ? t("common:loading")
              : mode === "edit"
                ? t("common:save")
                : t("common:add", { defaultValue: "Add" })}
          </button>
        </div>
      </div>
    </div>
  );
}

function PresetChip({
  selected,
  onClick,
  label,
  iconKey,
  recommended,
  dataTestid,
  disabled,
  title,
}: {
  selected: boolean;
  onClick: () => void;
  label: string;
  iconKey?: string;
  recommended?: boolean;
  dataTestid?: string;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      data-testid={dataTestid}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`group inline-flex items-center justify-start gap-1.5 truncate rounded-[8px] border px-2.5 py-1.5 text-left text-[12px] transition disabled:cursor-not-allowed disabled:opacity-60 ${
        selected
          ? "border-accent bg-accent/10 text-accent"
          : recommended
            ? "border-amber-300/40 bg-bg-grad-a/35 text-text-2 hover:border-accent/40"
            : "border-hairline bg-bg-grad-a/35 text-text-2 hover:border-accent/40"
      }`}
    >
      {recommended && (
        <Star
          className="h-3 w-3 shrink-0 fill-amber-300 text-amber-300"
          aria-label="recommended"
        />
      )}
      {iconKey && <PresetIcon iconKey={iconKey} size={14} />}
      <span className="truncate">{label}</span>
    </button>
  );
}

function Field({
  label,
  htmlFor,
  children,
  trailing,
}: {
  label: string;
  htmlFor?: string;
  children: React.ReactNode;
  trailing?: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <label
          htmlFor={htmlFor}
          className="text-[11.5px] font-medium text-text-2"
        >
          {label}
        </label>
        {trailing}
      </div>
      {children}
    </div>
  );
}
