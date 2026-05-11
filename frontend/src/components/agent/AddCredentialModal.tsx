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
  TestConnectionResponse,
} from "@/types/agent-credential";
import type { CustomProviderInfo } from "@/types/custom-provider";
import { errMsg } from "@/utils/async";

import { PresetIcon } from "./PresetIcon";
import { TestResultPanel } from "./TestResultPanel";

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

  // 草稿态连接测试：保存前先验 base_url + api_key 是否能真实跑通
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestConnectionResponse | null>(null);
  const [testedBaseUrl, setTestedBaseUrl] = useState<string | null>(null);

  // 异步竞态隔离：modal 重开（或父组件切到另一条凭证）后，旧 session 里
  // discover/test/import 的 await 仍可能返回并写 state。每次 reset effect 里
  // bump 一次，async 路径在 await 后比对 session id，不一致则丢弃结果。
  const sessionRef = useRef(0);

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

  // 父组件复用 modal 时只切换 open/initial，本地一次性诊断状态不会自动清。
  // 重开（或切换到另一条凭证）时把模型列表、错误、测试结果、popover、inflight
  // loading 全部归零，按新 initial 重算 advancedOpen，bump sessionRef 让旧
  // session 的 await 返回时丢弃结果。
  useEffect(() => {
    sessionRef.current += 1;
    if (!open) return;
    setModelOptions([]);
    setDiscoverError(null);
    setSubmitError(null);
    setTestResult(null);
    setTestedBaseUrl(null);
    setImportPickerOpen(false);
    setDiscovering(false);
    setTesting(false);
    setImporting(false);
    setAdvancedOpen(
      mode === "edit" &&
        Boolean(
          initial?.haiku_model || initial?.sonnet_model || initial?.opus_model || initial?.subagent_model,
        ),
    );
  }, [open, initial, mode]);

  const selected: PresetProvider | null = useMemo(() => {
    if (form.presetId === customSentinelId) return null;
    return presets.find((p) => p.id === form.presetId) ?? null;
  }, [form.presetId, presets, customSentinelId]);

  useEscapeClose(onClose, open);

  if (!open) return null;

  // 草稿任意可影响连通性的字段（preset / base_url / api_key / model）变化后，
  // 旧 testResult 已经不对应当前草稿了，必须失效，避免用户把未重新验证的配置
  // 当成已通过验证。
  const invalidateDraftTest = () => {
    setTestResult(null);
    setTestedBaseUrl(null);
  };

  // modelOptions 是按 (endpoint, credential) 元组发现出来的；base_url 或 api_key
  // 变了，旧列表里的 id 在新 endpoint 不一定支持，让它失效避免用户保存无效配置。
  const invalidateDiscoveredModels = () => {
    setModelOptions([]);
    setDiscoverError(null);
  };

  const handlePresetClick = (id: string) => {
    form.setPreset(id);
    invalidateDiscoveredModels();
    invalidateDraftTest();
  };

  const handleDiscover = async () => {
    const session = sessionRef.current;
    setDiscovering(true);
    setDiscoverError(null);
    try {
      // 优先使用表单里的 base_url：用户改了 URL 但发现仍走预设默认端点会选到
      // 当前 endpoint 不支持的模型。无 base_url 时回退到预设的 discovery/messages URL。
      const discoverBase =
        form.baseUrl.trim() ||
        (form.presetId === customSentinelId
          ? ""
          : selected?.discovery_url || selected?.messages_url || "");
      if (!discoverBase) {
        if (session === sessionRef.current) setDiscoverError(t("discover_no_base"));
        return;
      }
      if (!form.apiKey.trim()) {
        if (session === sessionRef.current) setDiscoverError(t("discover_api_key_required"));
        return;
      }
      const res = await API.discoverAnthropicModels({
        base_url: discoverBase,
        api_key: form.apiKey,
      });
      if (session !== sessionRef.current) return;
      setModelOptions(res.models.map((m) => m.model_id));
      const toast = useAppStore.getState().pushToast;
      if (res.models.length === 0) {
        toast(t("discover_no_models"), "warning");
      } else {
        toast(t("discover_models_success", { count: res.models.length }), "success");
      }
    } catch (err) {
      if (session === sessionRef.current) setDiscoverError(errMsg(err));
    } finally {
      if (session === sessionRef.current) setDiscovering(false);
    }
  };

  const handleImportProvider = async (provider: CustomProviderInfo) => {
    // 同 session 防重入：popover 内 provider option 没有 disabled，用户可以
    // 连击或在 inflight 期间点别的 provider；sessionRef 只挡跨 session race，
    // 挡不住同一 session 内的并发，最后返回的请求会覆盖表单。
    if (importing) return;
    const session = sessionRef.current;
    setImporting(true);
    // 立即关闭 popover，避免 inflight 期间用户继续看到可点选项
    setImportPickerOpen(false);
    try {
      const cred = await API.getCustomProviderCredentials(provider.id);
      if (session !== sessionRef.current) return;
      // 切到 __custom__：避免预设的 messages_url 覆盖刚导入的 base_url
      form.setPreset(customSentinelId);
      form.setApiKey(cred.api_key);
      form.setBaseUrl(cred.base_url);
      if (!form.displayName.trim()) {
        form.setDisplayName(provider.display_name);
      }
      invalidateDiscoveredModels();
      invalidateDraftTest();
      useAppStore
        .getState()
        .pushToast(t("import_provider_success", { name: provider.display_name }), "success");
    } catch (err) {
      if (session === sessionRef.current) {
        useAppStore.getState().pushToast(errMsg(err), "error");
      }
    } finally {
      if (session === sessionRef.current) setImporting(false);
    }
  };

  const handleTest = async () => {
    const session = sessionRef.current;
    setTesting(true);
    // 失败时清旧的"连接成功"面板，避免用户看到上一次的过期结果
    setTestResult(null);
    const submitBaseUrl = form.baseUrl.trim() || undefined;
    setTestedBaseUrl(submitBaseUrl ?? null);
    try {
      const res = await API.testAgentConnectionDraft({
        preset_id: form.presetId,
        base_url: submitBaseUrl,
        api_key: form.apiKey,
        model: form.model || undefined,
      });
      if (session !== sessionRef.current) return;
      setTestResult(res);
    } catch (err) {
      if (session === sessionRef.current) {
        useAppStore.getState().pushToast(errMsg(err), "error");
      }
    } finally {
      if (session === sessionRef.current) setTesting(false);
    }
  };

  const handleApplyFix = (suggestedBaseUrl: string) => {
    form.setBaseUrl(suggestedBaseUrl);
    // base_url 变了 → 旧 discovery 和测试结果都不再可信，鼓励用户重新发现+测试
    invalidateDiscoveredModels();
    invalidateDraftTest();
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
              aria-label={t("common:close")}
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
              onChange={(e) => {
                form.setBaseUrl(e.target.value);
                invalidateDiscoveredModels();
                invalidateDraftTest();
              }}
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
              onChange={(e) => {
                form.setApiKey(e.target.value);
                invalidateDiscoveredModels();
                invalidateDraftTest();
              }}
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
              onChange={(v) => {
                form.setModel(v);
                invalidateDraftTest();
              }}
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

          {testResult && (
            <TestResultPanel
              originalBaseUrl={testedBaseUrl}
              result={testResult}
              onApplyFix={handleApplyFix}
            />
          )}
        </div>

        {/* Footer */}
        <div className="mt-5 flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => void handleTest()}
            disabled={testing || submitting || !form.apiKey.trim() || !form.baseUrl.trim()}
            className={GHOST_BTN_CLS}
            data-testid="test-connection"
          >
            {testing ? (
              <Loader2 className="mr-1 inline-block h-3 w-3 motion-safe:animate-spin" aria-hidden />
            ) : null}
            {testing ? t("cred_testing") : t("cred_test_label")}
          </button>
          <div className="flex gap-2">
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
                  : t("common:add")}
            </button>
          </div>
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
  const { t } = useTranslation("common");
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
          aria-label={t("recommended")}
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
