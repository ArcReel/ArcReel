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

// Official Anthropic presets leave empty routing tiers to Claude Code rather than filling default models (matching lib/agent/credential_tiers.py).
const ANTHROPIC_OFFICIAL_PRESET_ID = "anthropic-official";

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
  // Import custom providers: list providers with configured api_key and prefill baseUrl after selection;
  // submit provider IDs only, letting servers copy keys from providers without frontend plaintext handling.
  const [providers, setProviders] = useState<CustomProviderInfo[]>([]);
  const [importSource, setImportSource] = useState<CustomProviderInfo | null>(null);

  // Draft connection tests: verify base_url + api_key actually work before saving.
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestConnectionResponse | null>(null);

  // Isolate async races: discover/test awaits may return and write state after modals close.
  // Async paths compare session IDs after await, discarding mismatched results.
  const sessionRef = useRef(0);

  useEffect(() => {
    if (!open || mode !== "create") return;
    let cancelled = false;
    // Clear old lists before fetching: failures cannot retain previous providers (the same React component instance
    // retains state across modal sessions), preventing selections of deleted/invalid providers that trigger 404.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Modal opening is external; synchronously clear previous lists before fetching
    setProviders([]);
    void (async () => {
      try {
        const res = await API.listCustomProviders();
        if (!cancelled) {
          setProviders(res.providers.filter((p) => p.api_key_masked));
        }
      } catch {
        // Silent: optional import shortcuts must not interrupt primary flows on failure.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, mode]);

  // Parents change keys to remount each opening, rebuilding forms/diagnostics from initial state. Closing/unmounting rotates
  // sessions so discover/test results returning while closed cannot match, write state or show notifications.
  useEffect(
    () => () => {
      sessionRef.current += 1;
    },
    [open],
  );

  const selected: PresetProvider | null = useMemo(() => {
    if (form.presetId === customSentinelId) return null;
    return presets.find((p) => p.id === form.presetId) ?? null;
  }, [form.presetId, presets, customSentinelId]);

  // When providers lack model-list APIs (e.g. the two Volcengine Ark subscription tiers), selects fall back to recommended preset models
  // so users can choose usable values without knowing model IDs.
  const availableModels = modelOptions.length > 0 ? modelOptions : (selected?.suggested_models ?? []);

  // Previews show fixed Agent runtime destinations: store base_url unchanged; CLI SDKs append only /v1/messages.
  const baseUrlRejected = hasUnsupportedUrlComponents(form.baseUrl);
  const messagesUrlPreview = form.baseUrl.trim() ? anthropicMessagesUrl(form.baseUrl) : "";

  // Changes to connectivity-relevant draft fields (preset / base_url / api_key / model) mean
  // previous testResult no longer describes current drafts; invalidate it so users never mistake unverified configurations
  // for verified ones.
  const invalidateDraftTest = () => {
    setTestResult(null);
  };

  // modelOptions are discovered by (endpoint, credential) tuples; changed base_url/api_key may leave old
  // IDs unsupported at new endpoints, so invalidate lists to prevent saving unusable configurations.
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
      // Discover against overridden base_url values; preset default endpoints might select models unsupported by current endpoints.
      // Preset selection prefills messages_url, which is not an override: fall back to preset catalog
      // discovery_url (DeepSeek and other preset model lists are outside messages roots). Detect overrides using the same
      // normalized comparison as saving; a trailing slash alone still means preset default.
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
    // Switching to __custom__: avoid overwriting imported base_url with preset messages_url.
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
    // Clear previous Connection successful panels on failure, avoiding stale results.
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
      // Unchanged prefilled URLs omit base_url so servers read provider URLs at submission; edited URLs submit as overrides.
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
    form.multiplierInvalid ||
    (mode === "edit" && !form.isDirty(initial));

  const closeUnlessSubmitting = (nextOpen: boolean) => {
    // Ignore Esc/backdrop clicks while submitting so dialogs cannot disappear before results.
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
              {/* Custom stays in the first cell, followed by recommendations. */}
              <ToggleGroup
                aria-labelledby="cred-preset-label"
                variant="outline"
                value={[form.presetId]}
                onValueChange={(next: string[]) => {
                  // Single selection: clicking selected options again does not deselect.
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
                    <p className="text-xs text-muted-foreground">
                      {t(
                        form.presetId === ANTHROPIC_OFFICIAL_PRESET_ID
                          ? "model_routing_hint_official"
                          : "model_routing_hint",
                      )}
                    </p>
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

            <Field label={t("cred_multiplier_label")} htmlFor="cred-multiplier">
              <Input
                id="cred-multiplier"
                inputMode="decimal"
                autoComplete="off"
                className="w-32"
                value={form.multiplierText}
                onChange={(e) => form.setMultiplierText(e.target.value)}
                aria-invalid={form.multiplierInvalid}
                aria-describedby="cred-multiplier-hint"
              />
              <p
                id="cred-multiplier-hint"
                className={form.multiplierInvalid ? "text-xs text-warn" : "text-xs text-muted-foreground"}
              >
                {form.multiplierInvalid ? t("cred_multiplier_invalid") : t("cred_multiplier_help")}
              </p>
            </Field>

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
