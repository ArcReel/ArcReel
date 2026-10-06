import { Download, ExternalLink, Loader2, Search, SlidersHorizontal, Star } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useCredentialForm } from "@/hooks/useCredentialForm";
import { useAppStore } from "@/stores/app-store";
import type {
  CreateAgentCredentialRequest,
  PresetProvider,
  TestConnectionResponse,
} from "@/types/agent-credential";
import type { CustomProviderInfo } from "@/types/custom-provider";
import {
  anthropicMessagesUrl,
  hasUnsupportedUrlComponents,
  normalizeAnthropicBaseUrl,
} from "@/utils/anthropic-url";
import { errMsg } from "@/utils/async";

import { ModelIdField } from "./ModelIdField";
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
  // 从自定义供应商导入：列出已配置 api_key 的 providers，选中后预填 baseUrl，
  // 提交时只带供应商 id，密钥由服务端从该供应商复制，前端不经手明文。
  const [providers, setProviders] = useState<CustomProviderInfo[]>([]);
  const [importSource, setImportSource] = useState<CustomProviderInfo | null>(null);

  // 草稿态连接测试：保存前先验 base_url + api_key 是否能真实跑通
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestConnectionResponse | null>(null);

  // 异步竞态隔离：modal 重开（或父组件切到另一条凭证）后，旧 session 里
  // discover/test 的 await 仍可能返回并写 state。每次 reset effect 里
  // bump 一次，async 路径在 await 后比对 session id，不一致则丢弃结果。
  const sessionRef = useRef(0);

  useEffect(() => {
    if (!open || mode !== "create") return;
    let cancelled = false;
    // 拉取前先清旧列表：失败时不会残留上一轮 providers（同一 React 组件实例
    // 跨 modal 会话保留 state），避免用户点到已删除/失效的 provider 触发 404。
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 打开 modal 是外部事件，拉取前须同步清空上一轮列表
    setProviders([]);
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
  // 重开（或切换到另一条凭证）时把模型列表、错误、测试结果、inflight
  // loading 全部归零，按新 initial 重算 advancedOpen，bump sessionRef 让旧
  // session 的 await 返回时丢弃结果。
  useEffect(() => {
    sessionRef.current += 1;
    if (!open) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 重开 modal 时的批量重置，是动作驱动的状态归零
    setModelOptions([]);
    setDiscoverError(null);
    setSubmitError(null);
    setTestResult(null);
    setDiscovering(false);
    setTesting(false);
    setImportSource(null);
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

  // 供应商不提供模型列表接口时（如火山方舟两档套餐），下拉框回退到预设的建议模型，
  // 用户不必知道模型 ID 也能选中一个可用值。
  const availableModels = modelOptions.length > 0 ? modelOptions : (selected?.suggested_models ?? []);

  // 预览即 Agent 运行时固定调用的地址：base_url 原样存储，CLI 内置 SDK 只在其后拼 /v1/messages
  const baseUrlRejected = hasUnsupportedUrlComponents(form.baseUrl);
  const messagesUrlPreview = form.baseUrl.trim() ? anthropicMessagesUrl(form.baseUrl) : "";

  // 草稿任意可影响连通性的字段（preset / base_url / api_key / model）变化后，
  // 旧 testResult 已经不对应当前草稿了，必须失效，避免用户把未重新验证的配置
  // 当成已通过验证。
  const invalidateDraftTest = () => {
    setTestResult(null);
  };

  // modelOptions 是按 (endpoint, credential) 元组发现出来的；base_url 或 api_key
  // 变了，旧列表里的 id 在新 endpoint 不一定支持，让它失效避免用户保存无效配置。
  const invalidateDiscoveredModels = () => {
    setModelOptions([]);
    setDiscoverError(null);
  };

  const handlePresetClick = (id: string) => {
    setImportSource(null);
    form.setPreset(id);
    invalidateDiscoveredModels();
    invalidateDraftTest();
  };

  const handleDiscover = async () => {
    if (baseUrlRejected) {
      setDiscoverError(t("base_url_unsupported_components"));
      return;
    }
    const session = sessionRef.current;
    setDiscovering(true);
    setDiscoverError(null);
    try {
      // 用户覆盖了 base_url 时按覆盖值发现：仍走预设默认端点会选到当前 endpoint 不支持的模型。
      // 选预设时表单预填的是预设的 messages_url，它不是覆盖：此时回退到预设目录的
      // discovery_url（DeepSeek 等预设的模型列表不在 messages 根之下）。是否覆盖按保存时
      // 同一套归一化后的值比较，只多一个尾斜杠仍算预设默认。
      const typedBase = form.baseUrl.trim();
      const isPresetDefault =
        form.presetId !== customSentinelId &&
        normalizeAnthropicBaseUrl(typedBase) === normalizeAnthropicBaseUrl(selected?.messages_url ?? "");
      const discoverBase =
        typedBase && !isPresetDefault
          ? typedBase
          : form.presetId === customSentinelId
            ? ""
            : selected?.discovery_url || selected?.messages_url || "";
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

  const handleImportProvider = (provider: CustomProviderInfo) => {
    // 切到 __custom__：避免预设的 messages_url 覆盖导入的 base_url
    form.setPreset(customSentinelId);
    form.setApiKey("");
    form.setBaseUrl(provider.base_url);
    if (!form.displayName.trim()) {
      form.setDisplayName(provider.display_name);
    }
    setImportSource(provider);
    invalidateDiscoveredModels();
    invalidateDraftTest();
    useAppStore
      .getState()
      .pushToast(t("import_provider_success", { name: provider.display_name }), "success");
  };

  const handleManualKeyEntry = () => {
    setImportSource(null);
    invalidateDiscoveredModels();
    invalidateDraftTest();
  };

  const handleTest = async () => {
    if (baseUrlRejected) {
      setSubmitError(t("base_url_unsupported_components"));
      return;
    }
    const session = sessionRef.current;
    setSubmitError(null);
    setTesting(true);
    // 失败时清旧的"连接成功"面板，避免用户看到上一次的过期结果
    setTestResult(null);
    const submitBaseUrl = form.baseUrl.trim() || undefined;
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

  const handleSubmit = async () => {
    if (baseUrlRejected) {
      setSubmitError(t("base_url_unsupported_components"));
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      const req = form.buildRequest();
      // 预填地址未改动时不带 base_url，由服务端取供应商提交时的地址；用户改过才作为覆盖提交
      const baseUrlOverride =
        importSource && form.baseUrl.trim() !== importSource.base_url ? req.base_url : undefined;
      await onSubmit(
        importSource
          ? {
              ...req,
              api_key: undefined,
              base_url: baseUrlOverride,
              from_custom_provider_id: importSource.id,
            }
          : req,
      );
      onClose();
    } catch (err) {
      setSubmitError(errMsg(err));
    } finally {
      setSubmitting(false);
    }
  };

  const submitDisabled =
    submitting ||
    (mode === "create" && !form.apiKey.trim() && !importSource) ||
    !form.baseUrl.trim() ||
    baseUrlRejected ||
    (mode === "edit" && !form.isDirty(initial));

  const closeUnlessSubmitting = (nextOpen: boolean) => {
    // 提交在途时不响应 Esc 与遮罩点击，避免对话框先于结果消失
    if (!nextOpen && !submitting) onClose();
  };

  return (
    <Dialog open={open} onOpenChange={closeUnlessSubmitting}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{mode === "edit" ? t("edit_credential_title") : t("add_credential")}</DialogTitle>
          <DialogDescription>{t("agent_provider_dialog_desc")}</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between gap-3">
                <span id="cred-preset-label" className="text-sm font-medium">
                  {t("select_provider")}
                </span>
                {mode === "create" && providers.length > 0 && (
                  <DropdownMenu>
                    <DropdownMenuTrigger
                      render={<Button variant="outline" size="xs" data-testid="import-from-provider" />}
                    >
                      <Download aria-hidden data-icon="inline-start" />
                      {t("import_from_provider")}
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-64">
                      {providers.map((p) => (
                        <DropdownMenuItem
                          key={p.id}
                          onClick={() => handleImportProvider(p)}
                          data-testid="import-provider-option"
                        >
                          <span className="truncate">{p.display_name}</span>
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </div>
              {/* 自定义固定在首格，推荐项次之 */}
              <ToggleGroup
                aria-labelledby="cred-preset-label"
                variant="outline"
                value={[form.presetId]}
                onValueChange={(next: string[]) => {
                  // 单选：再次点击已选项不取消选择
                  if (next[0]) handlePresetClick(next[0]);
                }}
                disabled={mode === "edit"}
                spacing={1.5}
                className="grid w-full grid-cols-3"
              >
                <ToggleGroupItem value={customSentinelId} data-testid="preset-chip" className="justify-start">
                  <span className="truncate">{t("custom_config")}</span>
                </ToggleGroupItem>
                {presets.map((p) => (
                  <ToggleGroupItem key={p.id} value={p.id} data-testid="preset-chip" className="min-w-0 justify-start">
                    {p.is_recommended && (
                      <Star role="img" aria-label={t("common:recommended")} className="fill-current text-primary" />
                    )}
                    <PresetIcon iconKey={p.icon_key} size={14} />
                    <span className="truncate">{p.display_name}</span>
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
              {mode === "edit" && <p className="text-xs text-muted-foreground">{t("preset_locked_in_edit")}</p>}
            </div>

            <Field label={t("display_name")} htmlFor="cred-name">
              <Input id="cred-name" value={form.displayName} onChange={(e) => form.setDisplayName(e.target.value)} />
            </Field>

            <Field label={t("api_base_url")} htmlFor="cred-url">
              <Input
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
                placeholder="https://api.example.com"
                aria-invalid={baseUrlRejected}
                aria-describedby="cred-url-preview"
              />
              <p id="cred-url-preview" className="text-xs">
                {baseUrlRejected ? (
                  <span className="text-warn">{t("base_url_unsupported_components")}</span>
                ) : messagesUrlPreview ? (
                  <span className="text-muted-foreground">
                    {t("messages_url_preview_hint")}{" "}
                    <span className="font-mono break-all" translate="no">
                      {messagesUrlPreview}
                    </span>
                  </span>
                ) : null}
              </p>
            </Field>

            <Field
              label={t("anthropic_api_key")}
              htmlFor="cred-key"
              trailing={
                importSource ? (
                  <Button variant="link" size="xs" onClick={handleManualKeyEntry} data-testid="api-key-manual-entry">
                    {t("api_key_manual_entry")}
                  </Button>
                ) : selected?.api_key_url ? (
                  <a
                    href={selected.api_key_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-xs text-primary underline-offset-4 hover:underline"
                  >
                    {t("get_api_key")}
                    <ExternalLink aria-hidden className="size-3" />
                  </a>
                ) : null
              }
            >
              <Input
                mono
                id="cred-key"
                type="password"
                value={form.apiKey}
                onChange={(e) => {
                  form.setApiKey(e.target.value);
                  invalidateDiscoveredModels();
                  invalidateDraftTest();
                }}
                disabled={importSource !== null}
                autoComplete="off"
                spellCheck={false}
                placeholder={
                  importSource
                    ? t("api_key_from_provider_hint", { name: importSource.display_name })
                    : mode === "edit"
                      ? t("api_key_unchanged_hint")
                      : undefined
                }
                aria-describedby={importSource ? "cred-key-import-note" : undefined}
              />
              {importSource && (
                <p id="cred-key-import-note" className="text-xs text-muted-foreground">
                  {t("api_key_from_provider_note")}
                </p>
              )}
            </Field>

            <Field
              label={t("default_model")}
              htmlFor="cred-model"
              trailing={
                <Button
                  variant="ghost"
                  size="xs"
                  onClick={() => void handleDiscover()}
                  disabled={discovering || importSource !== null}
                >
                  {discovering ? (
                    <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
                  ) : (
                    <Search aria-hidden data-icon="inline-start" />
                  )}
                  {discovering ? t("discovering_models") : t("discover_models")}
                </Button>
              }
            >
              <ModelIdField
                id="cred-model"
                value={form.model}
                onChange={(v) => {
                  form.setModel(v);
                  invalidateDraftTest();
                }}
                options={availableModels}
                placeholder={selected?.default_model || ""}
                aria-describedby={discoverError ? "cred-model-error" : undefined}
              />
              {discoverError && (
                <p id="cred-model-error" className="text-xs text-warn">
                  {discoverError}
                </p>
              )}
            </Field>

            <div className="rounded-lg border border-border p-1">
              <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
                <CollapsibleTrigger render={<Button variant="ghost" className="w-full justify-start" />}>
                  <SlidersHorizontal aria-hidden data-icon="inline-start" className="text-muted-foreground" />
                  {t("advanced_model_routing")}
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <div className="flex flex-col gap-4 px-3 pt-2 pb-3">
                    <p className="text-xs text-muted-foreground">{t("model_routing_hint")}</p>
                    <RoutingField
                      id="cred-haiku"
                      label={t("haiku_model")}
                      desc={t("haiku_desc")}
                      envVar="ANTHROPIC_DEFAULT_HAIKU_MODEL"
                      value={form.haikuModel}
                      onChange={form.setHaikuModel}
                      options={availableModels}
                    />
                    <RoutingField
                      id="cred-sonnet"
                      label={t("sonnet_model")}
                      desc={t("sonnet_desc")}
                      envVar="ANTHROPIC_DEFAULT_SONNET_MODEL"
                      value={form.sonnetModel}
                      onChange={form.setSonnetModel}
                      options={availableModels}
                    />
                    <RoutingField
                      id="cred-opus"
                      label={t("opus_model")}
                      desc={t("opus_desc")}
                      envVar="ANTHROPIC_DEFAULT_OPUS_MODEL"
                      value={form.opusModel}
                      onChange={form.setOpusModel}
                      options={availableModels}
                    />
                    <RoutingField
                      id="cred-subagent"
                      label={t("subagent_model")}
                      desc={t("subagent_desc")}
                      envVar="CLAUDE_CODE_SUBAGENT_MODEL"
                      value={form.subagentModel}
                      onChange={form.setSubagentModel}
                      options={availableModels}
                    />
                  </div>
                </CollapsibleContent>
              </Collapsible>
            </div>

            {selected?.notes && (
              <p className="rounded-lg border border-border bg-muted/50 px-3 py-2 text-sm text-subtle-foreground">
                {selected.notes}
              </p>
            )}

            {submitError && (
              <p role="alert" className="text-sm text-destructive">
                {submitError}
              </p>
            )}

            {testResult && <TestResultPanel result={testResult} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => void handleTest()}
            disabled={
              testing ||
              submitting ||
              importSource !== null ||
              !form.apiKey.trim() ||
              !form.baseUrl.trim() ||
              baseUrlRejected
            }
            data-testid="test-connection"
            className="mr-auto"
          >
            {testing ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
            {testing ? t("cred_testing") : t("cred_test_label")}
          </Button>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            {t("common:cancel")}
          </Button>
          <Button onClick={() => void handleSubmit()} disabled={submitDisabled}>
            {submitting ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
            {mode === "edit" ? t("common:save") : t("common:add")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  htmlFor,
  children,
  trailing,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
  trailing?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex min-h-6 items-center justify-between gap-3">
        <label htmlFor={htmlFor} className="text-sm font-medium">
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
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <p id={`${id}-desc`} className="text-xs text-muted-foreground">
        {desc}
      </p>
      <ModelIdField
        id={id}
        value={value}
        onChange={onChange}
        options={options}
        placeholder={envVar}
        aria-describedby={`${id}-desc`}
      />
    </div>
  );
}
