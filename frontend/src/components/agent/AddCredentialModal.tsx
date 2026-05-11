import {
  ChevronDown,
  Download,
  ExternalLink,
  Loader2,
  Search,
  SlidersHorizontal,
  Star,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import {
  ACCENT_BTN_CLS,
  ACCENT_BUTTON_STYLE,
  DROPDOWN_PANEL_STYLE,
  GHOST_BTN_CLS,
  INPUT_CLS,
} from "@/components/ui/darkroom-tokens";
import { ModelCombobox } from "@/components/ui/ModelCombobox";
import { Popover } from "@/components/ui/Popover";
import { useCredentialForm } from "@/hooks/useCredentialForm";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useAppStore } from "@/stores/app-store";
import type {
  CreateAgentCredentialRequest,
  PresetProvider,
} from "@/types/agent-credential";
import type { CustomProviderInfo } from "@/types/custom-provider";
import { errMsg } from "@/utils/async";

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
  const form = useCredentialForm(initial, customSentinelId, presets);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [discoverError, setDiscoverError] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(
    mode === "edit" &&
      Boolean(
        initial?.haiku_model || initial?.sonnet_model || initial?.opus_model || initial?.subagent_model,
      ),
  );
  // 从自定义供应商导入：列出已配置 api_key 的 providers，选中后填充 baseUrl + apiKey 草稿
  const [providers, setProviders] = useState<CustomProviderInfo[]>([]);
  const [importPickerOpen, setImportPickerOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const importTriggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open || mode !== "create") return;
    let cancelled = false;
    void (async () => {
      try {
        const res = await API.listCustomProviders();
        if (!cancelled) {
          setProviders(res.providers.filter((p) => p.api_key_masked));
        }
      } catch {
        // 静默：导入是可选快捷入口，失败不打断主流程
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, mode]);

  const selected: PresetProvider | null = useMemo(() => {
    if (form.presetId === customSentinelId) return null;
    return presets.find((p) => p.id === form.presetId) ?? null;
  }, [form.presetId, presets, customSentinelId]);

  useEscapeClose(onClose, open);

  if (!open) return null;

  const handlePresetClick = (id: string) => {
    form.setPreset(id);
    setModelOptions([]);
    setDiscoverError(null);
  };

  const handleDiscover = async () => {
    setDiscovering(true);
    setDiscoverError(null);
    try {
      // 预设模式：用预设 discovery_url（若有），否则用 messages_url 根；自定义：用用户填的 base_url
      const discoverBase =
        form.presetId === customSentinelId
          ? form.baseUrl
          : selected?.discovery_url || selected?.messages_url || "";
      if (!discoverBase) {
        setDiscoverError(t("discover_no_base"));
        return;
      }
      if (!form.apiKey.trim()) {
        setDiscoverError(t("discover_api_key_required"));
        return;
      }
      const res = await API.discoverAnthropicModels({
        base_url: discoverBase,
        api_key: form.apiKey,
      });
      setModelOptions(res.models.map((m) => m.model_id));
      if (res.models.length === 0) {
        setDiscoverError(t("discover_no_models"));
      }
    } catch (err) {
      setDiscoverError(errMsg(err));
    } finally {
      setDiscovering(false);
    }
  };

  const handleImportProvider = async (provider: CustomProviderInfo) => {
    setImporting(true);
    try {
      const cred = await API.getCustomProviderCredentials(provider.id);
      // 切到 __custom__：避免预设的 messages_url 覆盖刚导入的 base_url
      form.setPreset(customSentinelId);
      form.setApiKey(cred.api_key);
      form.setBaseUrl(cred.base_url);
      if (!form.displayName.trim()) {
        form.setDisplayName(provider.display_name);
      }
      setModelOptions([]);
      setDiscoverError(null);
      useAppStore
        .getState()
        .pushToast(t("import_provider_success", { name: provider.display_name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setImporting(false);
      setImportPickerOpen(false);
    }
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      await onSubmit(form.buildRequest());
      onClose();
    } catch (err) {
      setSubmitError(errMsg(err));
    } finally {
      setSubmitting(false);
    }
  };

  const submitDisabled =
    submitting ||
    (mode === "create" && !form.apiKey.trim()) ||
    !form.baseUrl.trim() ||
    (mode === "edit" && !form.isDirty(initial));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center px-4">
      <div
        data-testid="modal-overlay"
        aria-hidden="true"
        onClick={onClose}
        className="absolute inset-0 bg-black/50"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="cred-modal-title"
        className="relative max-h-[90vh] w-full max-w-2xl overflow-y-auto overscroll-contain rounded-[12px] border border-hairline p-5"
        style={DROPDOWN_PANEL_STYLE}
      >
        {/* Header */}
        <div className="mb-4 flex items-start justify-between gap-3">
          <h3
            id="cred-modal-title"
            className="text-[15px] font-medium text-text"
          >
            {mode === "edit" ? t("edit_credential_title") : t("add_credential")}
          </h3>
          <div className="flex items-center gap-2">
            {mode === "create" && providers.length > 0 && (
              <>
                <button
                  ref={importTriggerRef}
                  type="button"
                  onClick={() => setImportPickerOpen((v) => !v)}
                  disabled={importing}
                  data-testid="import-from-provider"
                  className="inline-flex items-center gap-1.5 rounded-[6px] border border-hairline px-2 py-1 font-mono text-[10px] uppercase tracking-[0.14em] text-text-2 transition hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {importing ? (
                    <Loader2 className="h-3 w-3 motion-safe:animate-spin" aria-hidden />
                  ) : (
                    <Download className="h-3 w-3" aria-hidden />
                  )}
                  {t("import_from_provider")}
                </button>
                <Popover
                  open={importPickerOpen}
                  onClose={() => setImportPickerOpen(false)}
                  anchorRef={importTriggerRef}
                  width="w-64"
                  // modal 容器是 z-50；默认 Popover layer 是 z-40 会被 modal 遮挡
                  layer="modal"
                  className="rounded-[8px] border border-hairline py-1 shadow-lg"
                >
                  {providers.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => void handleImportProvider(p)}
                      data-testid="import-provider-option"
                      className="block w-full truncate px-3 py-2 text-left text-[12px] text-text-2 hover:bg-bg-grad-a/50"
                    >
                      {p.display_name}
                    </button>
                  ))}
                </Popover>
              </>
            )}
            <button
              type="button"
              onClick={onClose}
              className="text-text-3 hover:text-text"
              aria-label="close"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Preset grid — 3 列固定网格,自定义永远固定首格,推荐项次之 */}
        <div className="mb-5">
          <div className="mb-2 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-text-2">
            {t("select_provider")}
          </div>
          <div className="grid grid-cols-3 gap-1.5">
            <PresetChip
              dataTestid="preset-chip"
              selected={form.presetId === customSentinelId}
              onClick={() => handlePresetClick(customSentinelId)}
              label={t("custom_config")}
              disabled={mode === "edit"}
              title={mode === "edit" ? t("preset_locked_in_edit") : undefined}
            />
            {presets.map((p) => (
              <PresetChip
                key={p.id}
                dataTestid="preset-chip"
                selected={form.presetId === p.id}
                onClick={() => handlePresetClick(p.id)}
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
              value={form.displayName}
              onChange={(e) => form.setDisplayName(e.target.value)}
              className={INPUT_CLS}
            />
          </Field>

          <Field label={t("api_base_url")} htmlFor="cred-url">
            <input
              id="cred-url"
              type="url"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              value={form.baseUrl}
              onChange={(e) => form.setBaseUrl(e.target.value)}
              placeholder="https://api.example.com/anthropic"
              className={INPUT_CLS}
            />
          </Field>

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
              value={form.apiKey}
              onChange={(e) => form.setApiKey(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              placeholder={mode === "edit" ? t("api_key_unchanged_hint") : undefined}
              className={INPUT_CLS}
            />
          </Field>

          <Field
            label={t("default_model")}
            htmlFor="cred-model"
            trailing={
              <button
                type="button"
                onClick={() => void handleDiscover()}
                disabled={discovering}
                className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.14em] text-text-3 transition-colors hover:text-accent-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {discovering ? (
                  <Loader2 className="h-3 w-3 motion-safe:animate-spin" aria-hidden />
                ) : (
                  <Search className="h-3 w-3" aria-hidden />
                )}
                {discovering ? t("discovering_models") : t("discover_models")}
              </button>
            }
          >
            <ModelCombobox
              id="cred-model"
              value={form.model}
              onChange={form.setModel}
              options={modelOptions}
              placeholder={selected?.default_model || ""}
              clearable
            />
            {discoverError && (
              <div className="mt-1 text-[11px] text-warm-bright">{discoverError}</div>
            )}
          </Field>

          {/* Advanced model routing - 折叠区 */}
          <details
            open={advancedOpen}
            onToggle={(e) => setAdvancedOpen(e.currentTarget.open)}
            className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/35 p-3"
          >
            <summary className="flex cursor-pointer list-none items-center justify-between">
              <span className="inline-flex items-center gap-2 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-text-2">
                <SlidersHorizontal className="h-3.5 w-3.5 text-accent-2" aria-hidden />
                {t("advanced_model_routing")}
              </span>
              <span className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-hairline-soft bg-bg-grad-a/55 text-text-3">
                <ChevronDown
                  className={`h-3 w-3 transition-transform duration-200 ${
                    advancedOpen ? "rotate-180 text-accent-2" : ""
                  }`}
                  aria-hidden
                />
              </span>
            </summary>
            <p className="mt-2 text-[11px] leading-[1.55] text-text-3">
              {t("model_routing_hint")}
            </p>
            <div className="mt-3 grid gap-3">
              <RoutingField
                id="cred-haiku"
                label={t("haiku_model")}
                desc={t("haiku_desc")}
                envVar="ANTHROPIC_DEFAULT_HAIKU_MODEL"
                value={form.haikuModel}
                onChange={form.setHaikuModel}
                options={modelOptions}
              />
              <RoutingField
                id="cred-sonnet"
                label={t("sonnet_model")}
                desc={t("sonnet_desc")}
                envVar="ANTHROPIC_DEFAULT_SONNET_MODEL"
                value={form.sonnetModel}
                onChange={form.setSonnetModel}
                options={modelOptions}
              />
              <RoutingField
                id="cred-opus"
                label={t("opus_model")}
                desc={t("opus_desc")}
                envVar="ANTHROPIC_DEFAULT_OPUS_MODEL"
                value={form.opusModel}
                onChange={form.setOpusModel}
                options={modelOptions}
              />
              <RoutingField
                id="cred-subagent"
                label={t("subagent_model")}
                desc={t("subagent_desc")}
                envVar="CLAUDE_CODE_SUBAGENT_MODEL"
                value={form.subagentModel}
                onChange={form.setSubagentModel}
                options={modelOptions}
              />
            </div>
          </details>

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
          <button type="button" onClick={onClose} className={GHOST_BTN_CLS}>
            {t("common:cancel")}
          </button>
          <button
            type="button"
            onClick={() => void handleSubmit()}
            disabled={submitDisabled}
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

function RoutingField({
  id,
  label,
  desc,
  envVar,
  value,
  onChange,
  options,
}: {
  id: string;
  label: string;
  desc: string;
  envVar: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  return (
    <div>
      <label htmlFor={id} className="block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-2">
        {label}
      </label>
      <div className="text-[11px] text-text-4">{desc}</div>
      <div className="mt-1.5">
        <ModelCombobox
          id={id}
          value={value}
          onChange={onChange}
          options={options}
          placeholder={envVar}
          aria-label={label}
          clearable
        />
      </div>
    </div>
  );
}
