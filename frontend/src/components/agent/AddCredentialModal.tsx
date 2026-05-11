import { ChevronDown, ExternalLink, Loader2, Search, SlidersHorizontal, Star, X } from "lucide-react";
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
import { useFocusTrap } from "@/hooks/useFocusTrap";
import type {
  CreateAgentCredentialRequest,
  PresetProvider,
} from "@/types/agent-credential";
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
  const [presetId, setPresetId] = useState<string>(
    initial?.preset_id ?? customSentinelId,
  );
  const [apiKey, setApiKey] = useState<string>(initial?.api_key ?? "");
  const [baseUrl, setBaseUrl] = useState<string>(initial?.base_url ?? "");
  const [displayName, setDisplayName] = useState<string>(
    initial?.display_name ?? "",
  );
  const [model, setModel] = useState<string>(initial?.model ?? "");
  const [haikuModel, setHaikuModel] = useState<string>(initial?.haiku_model ?? "");
  const [sonnetModel, setSonnetModel] = useState<string>(initial?.sonnet_model ?? "");
  const [opusModel, setOpusModel] = useState<string>(initial?.opus_model ?? "");
  const [subagentModel, setSubagentModel] = useState<string>(initial?.subagent_model ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // Discover state — 共享给 5 个 ModelCombobox 的 options
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [discoverError, setDiscoverError] = useState<string | null>(null);
  // 高级路由折叠 - edit 模式下若已存在任一路由值则默认展开
  const initialAdvancedOpen =
    mode === "edit" &&
    Boolean(
      initial?.haiku_model || initial?.sonnet_model || initial?.opus_model || initial?.subagent_model,
    );
  const [advancedOpen, setAdvancedOpen] = useState(initialAdvancedOpen);

  const selected: PresetProvider | null = useMemo(() => {
    if (presetId === customSentinelId) return null;
    return presets.find((p) => p.id === presetId) ?? null;
  }, [presetId, presets, customSentinelId]);

  // 切换预设时同步 display_name 默认值;model/routing 永不预填(由用户主动决定)
  useEffect(() => {
    if (selected) {
      setDisplayName((cur) => cur || selected.display_name);
    }
  }, [selected]);

  // 打开 modal 时按 initial 重置(edit 模式切换不同凭证 / create 复用)
  useEffect(() => {
    if (!open) return;
    setPresetId(initial?.preset_id ?? customSentinelId);
    setApiKey(initial?.api_key ?? "");
    setBaseUrl(initial?.base_url ?? "");
    setDisplayName(initial?.display_name ?? "");
    setModel(initial?.model ?? "");
    setHaikuModel(initial?.haiku_model ?? "");
    setSonnetModel(initial?.sonnet_model ?? "");
    setOpusModel(initial?.opus_model ?? "");
    setSubagentModel(initial?.subagent_model ?? "");
    setSubmitError(null);
    setModelOptions([]);
    setDiscoverError(null);
    // 仅在 open 状态切换或 initial 引用变化时同步
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initial]);

  const reset = () => {
    setPresetId(customSentinelId);
    setApiKey("");
    setBaseUrl("");
    setDisplayName("");
    setModel("");
    setHaikuModel("");
    setSonnetModel("");
    setOpusModel("");
    setSubagentModel("");
    setSubmitError(null);
    setModelOptions([]);
    setDiscoverError(null);
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

  const handlePresetClick = (id: string) => {
    if (id === presetId) return;
    setPresetId(id);
    // 切换预设/自定义时清空所有 model 字段(用户反馈:默认不要预填充)
    setModel("");
    setHaikuModel("");
    setSonnetModel("");
    setOpusModel("");
    setSubagentModel("");
    setModelOptions([]);
    setDiscoverError(null);
    // base_url & display_name:切预设 = 用该预设的默认值覆盖;切自定义 = 清空.
    // 注:覆盖即使用户已改过 - 用户反馈期望"切换预设时名称跟着切换".
    if (id === customSentinelId) {
      setBaseUrl("");
      setDisplayName("");
    } else {
      const next = presets.find((p) => p.id === id);
      setBaseUrl(next?.messages_url ?? "");
      setDisplayName(next?.display_name ?? "");
    }
  };

  const handleDiscover = async () => {
    setDiscovering(true);
    setDiscoverError(null);
    try {
      // 草稿态用 discoverAnthropicModels(base_url, api_key)。
      // 预设模式:用预设的 discovery_url(若有);否则用 messages_url 的根。
      // 自定义模式:用用户填的 base_url。
      const discoverBase =
        presetId === customSentinelId
          ? baseUrl
          : selected?.discovery_url || selected?.messages_url || "";
      if (!discoverBase) {
        setDiscoverError(t("discover_no_base"));
        return;
      }
      if (!apiKey.trim()) {
        setDiscoverError(t("discover_api_key_required"));
        return;
      }
      const res = await API.discoverAnthropicModels({
        base_url: discoverBase,
        api_key: apiKey,
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

  const handleSubmit = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const req: CreateAgentCredentialRequest = {
        preset_id: presetId,
        api_key: apiKey,
        display_name: displayName || undefined,
        // 预设模式也透传 base_url:用户可在该字段改写预设默认值
        base_url: baseUrl || undefined,
        model: model || undefined,
        haiku_model: haikuModel || undefined,
        sonnet_model: sonnetModel || undefined,
        opus_model: opusModel || undefined,
        subagent_model: subagentModel || undefined,
      };
      await onSubmit(req);
      reset();
      onClose();
    } catch (err) {
      setSubmitError(errMsg(err));
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

        {/* Preset grid — 3 列固定网格,自定义永远固定首格,推荐项次之 */}
        <div className="mb-5">
          <div className="mb-2 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-text-2">
            {t("select_provider")}
          </div>
          <div className="grid grid-cols-3 gap-1.5">
            <PresetChip
              dataTestid="preset-chip"
              selected={presetId === customSentinelId}
              onClick={() => handlePresetClick(customSentinelId)}
              label={t("custom_config")}
              disabled={mode === "edit"}
              title={mode === "edit" ? t("preset_locked_in_edit") : undefined}
            />
            {presets.map((p) => (
              <PresetChip
                key={p.id}
                dataTestid="preset-chip"
                selected={presetId === p.id}
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
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className={INPUT_CLS}
            />
          </Field>

          <Field label={t("api_base_url")} htmlFor="cred-url">
            <input
              id="cred-url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
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
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              autoComplete="off"
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
                {t("discover_models")}
              </button>
            }
          >
            <ModelCombobox
              id="cred-model"
              value={model}
              onChange={setModel}
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
                value={haikuModel}
                onChange={setHaikuModel}
                options={modelOptions}
              />
              <RoutingField
                id="cred-sonnet"
                label={t("sonnet_model")}
                desc={t("sonnet_desc")}
                envVar="ANTHROPIC_DEFAULT_SONNET_MODEL"
                value={sonnetModel}
                onChange={setSonnetModel}
                options={modelOptions}
              />
              <RoutingField
                id="cred-opus"
                label={t("opus_model")}
                desc={t("opus_desc")}
                envVar="ANTHROPIC_DEFAULT_OPUS_MODEL"
                value={opusModel}
                onChange={setOpusModel}
                options={modelOptions}
              />
              <RoutingField
                id="cred-subagent"
                label={t("subagent_model")}
                desc={t("subagent_desc")}
                envVar="CLAUDE_CODE_SUBAGENT_MODEL"
                value={subagentModel}
                onChange={setSubagentModel}
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
          <button onClick={onClose} className={GHOST_BTN_CLS}>
            {t("common:cancel")}
          </button>
          <button
            onClick={() => void handleSubmit()}
            disabled={
              submitting ||
              (mode === "create" && !apiKey.trim()) ||
              !baseUrl.trim()
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
