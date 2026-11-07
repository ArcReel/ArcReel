import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { CheckCircle2, CloudDownload, Eye, EyeOff, Loader2, MoreHorizontal, Plus, Trash2, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { PartialSaveError, useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAppStore } from "@/stores/app-store";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type {
  CustomProviderInfo,
  EndpointKey,
  FetchedProviderModel,
  FetchProviderModelsResponse,
  ProviderMultiplierInfo,
} from "@/types";
import { errMsg, voidCall } from "@/utils/async";
import { DurationParseError } from "@/utils/duration_format";
import { formatNameList } from "@/utils/list-format";
import { parseMultiplier } from "@/utils/multiplier";
import { FieldHint, FieldRow } from "./CustomProviderFieldRow";
import { ModelTable } from "./CustomProviderModelTable";
import { DURATION_ERROR_KEY } from "./durationErrors";
import { FetchModelsDialog, type FetchSelection } from "./FetchModelsDialog";
import { ProviderModelList } from "./ProviderModelList";
import {
  isComfyuiEndpoint,
  isComfyuiProtocol,
  isComfyuiVideoEndpoint,
  mergeDiscoveredModels,
  toggleDefaultReducer,
  urlPreviewFor,
  type DiscoveryFormat,
} from "./customProviderHelpers";
import {
  DEFAULT_PROVIDER_MULTIPLIER,
  discoveredToFetched,
  effectiveModelRows,
  fetchedToRow,
  formFromProvider,
  newModelRow,
  newProviderDefaultMultiplier,
  newProviderForm,
  parseOptionalMultiplier,
  parsePositiveInt,
  rowToInput,
  type CustomProviderFormValue,
  type ModelRow,
} from "./customProviderFormState";

/** Custom-provider IDs in provider-configuration APIs. */
function customProviderKey(id: number): string {
  return `custom-${id}`;
}

const DISCOVERY_FORMAT_OPTIONS: { value: DiscoveryFormat; labelKey: string }[] = [
  { value: "openai", labelKey: "discovery_format_openai" },
  { value: "google", labelKey: "discovery_format_google" },
  { value: "comfyui", labelKey: "discovery_format_comfyui" },
];

interface CustomProviderFormProps {
  /** Saved providers; omission means creation forms. */
  existing?: CustomProviderInfo | null;
  /** Saved provider multipliers, fetched with `existing`; creation/omission defaults to 1. */
  providerMultiplier?: ProviderMultiplierInfo;
  /** Prefilled API addresses wired from Endpoints (definition meta.hints.base_url). */
  initialBaseUrl?: string;
  /** Preselected wired endpoints create initial model rows in new forms. */
  initialEndpoint?: EndpointKey;
  /** Deep-linked model IDs open their Sheets immediately. */
  focusModelId?: string;
  /** Called after save success with saved providers; newly created providers let callers select them. */
  onSaved: (provider?: CustomProviderInfo) => void;
  /** Called after successful deletion. */
  onDeleted?: () => void;
}

/**
 * Custom-provider details open as forms, without readonly states. Headers show protocols/API addresses; More menus contain Delete provider.
 * Names, API addresses, secrets, concurrency limits and provider multipliers share one edit unit submitted by bottom save bars.
 * Saved-provider models use lists: enablement applies immediately, other settings save individually in Sheets, outside this edit unit,
 * so whole saves never overwrite list edits. Creation forms create models with providers.
 */
export function CustomProviderForm({
  existing,
  providerMultiplier,
  initialBaseUrl,
  initialEndpoint,
  focusModelId,
  onSaved,
  onDeleted,
}: CustomProviderFormProps) {
  const { t, i18n } = useTranslation(["dashboard", "common"]);

  // Endpoint catalogs are sole backend truth for media types, mutually exclusive defaults and protocol pairing.
  const endpointToMediaType = useEndpointCatalogStore((s) => s.endpointToMediaType);
  const endpointToImageCapabilities = useEndpointCatalogStore((s) => s.endpointToImageCapabilities);
  const catalogEndpoints = useEndpointCatalogStore((s) => s.endpoints);
  const catalogInitialized = useEndpointCatalogStore((s) => s.initialized);
  const fetchEndpointCatalog = useEndpointCatalogStore((s) => s.fetch);
  useEffect(() => {
    void fetchEndpointCatalog();
  }, [fetchEndpointCatalog]);

  // Saved contents: creation forms establish these at mount and retain the same reference afterward.
  const [newForm] = useState(() => newProviderForm(initialBaseUrl, initialEndpoint));
  // Provider details exclude multipliers, read/written separately; saved PUT echoes are authoritative, provider refetches never carry them.
  const [savedMultiplier, setSavedMultiplier] = useState(providerMultiplier ?? DEFAULT_PROVIDER_MULTIPLIER);
  const source = useMemo(
    () => (existing ? formFromProvider(existing, savedMultiplier) : newForm),
    [existing, savedMultiplier, newForm],
  );

  // Wired endpoints determine protocols: ComfyUI endpoints pair only with ComfyUI providers (docs/adr/0081 bidirectional pairing),
  // so making users switch protocols themselves first forces save failures. Catalogs load asynchronously; derive values here
  // so protocols settle when catalogs arrive.
  const wiredFormat: DiscoveryFormat | null = useMemo(() => {
    const descriptor = catalogEndpoints.find((item) => item.key === initialEndpoint);
    return descriptor && isComfyuiEndpoint(descriptor) ? "comfyui" : null;
  }, [catalogEndpoints, initialEndpoint]);
  const formatOf = useCallback(
    (value: CustomProviderFormValue): DiscoveryFormat => value.pickedFormat ?? wiredFormat ?? "openai",
    [wiredFormat],
  );
  const deriveModels = useCallback(
    (value: CustomProviderFormValue) =>
      effectiveModelRows(
        value.models,
        catalogInitialized ? catalogEndpoints : null,
        isComfyuiProtocol(formatOf(value)),
      ),
    [catalogInitialized, catalogEndpoints, formatOf],
  );

  // Depart after deletion without guards: providers no longer exist, so unsaved edits have nowhere to persist.
  // (Postcreation navigation needs no bypass: while saves run, guards await completion and cleared edits before allowing.)
  const deletedRef = useRef(false);
  const allowNavigation = useCallback(() => deletedRef.current, []);

  const save = useCallback(
    async (
      value: CustomProviderFormValue,
      savedValue: CustomProviderFormValue,
    ): Promise<CustomProviderFormValue | void> => {
      const discoveryFormat = formatOf(value);
      const keyOptional = value.noApiKey || isComfyuiProtocol(discoveryFormat);
      const models = deriveModels(value);
      const enabledModels = models.filter((m) => m.is_enabled);
      // Save bars explain failures: user-missing fields first, then endpoint catalogs still loading.
      const fail = (message: string): never => {
        throw new Error(message);
      };
      if (!value.displayName.trim()) fail(t("fill_provider_name"));
      if (!value.baseUrl.trim()) fail(t("fill_base_url"));
      if (!existing && !keyOptional && !value.apiKey.trim()) fail(t("fill_api_key"));
      // Saved providers never edit models in forms (value.models always empty); following model checks apply to creation only.
      if (!existing && enabledModels.length === 0) fail(t("enable_one_model"));
      if (enabledModels.some((m) => !m.model_id.trim())) fail(t("enabled_model_needs_id"));
      // Unloaded catalogs cannot assess protocol compatibility; unresolved wired endpoints fall back to openai,
      // still yielding row 422. Without either condition, forms retain default protocols/endpoints and need no catalog lookup.
      const catalogPending =
        !catalogInitialized &&
        value.models.length > 0 &&
        (initialEndpoint !== undefined || value.pickedFormat !== null);
      if (catalogPending) fail(t("cp_endpoint_catalog_pending"));
      // Rows with unselected endpoints: servers validate pairings per row; one absent endpoint blocks the whole configuration.
      if (models.some((m) => !m.endpoint)) fail(t("cp_model_endpoint_unselected"));
      if (models.some((m) => parseMultiplier(m.multiplier_text) === undefined)) fail(t("cp_model_multiplier_invalid"));
      // Edit durations only for ComfyUI video rows, writing overrides only with derivable tiers; echo other row overrides unchanged.
      const durationsEditable = (endpoint: EndpointKey) => {
        const descriptor = catalogEndpoints.find((item) => item.key === endpoint);
        return descriptor !== undefined && isComfyuiVideoEndpoint(descriptor) && !descriptor.duration_tier_empty;
      };
      let payloadModels;
      try {
        payloadModels = models.map((m) => rowToInput(m, { durationsEditable: durationsEditable(m.endpoint) }));
      } catch (e) {
        if (e instanceof DurationParseError) {
          fail(t("supported_durations_invalid", { message: t(DURATION_ERROR_KEY[e.code], e.params) }));
        }
        throw e;
      }
      const imageMax = parsePositiveInt(value.imageMaxWorkers);
      const videoMax = parsePositiveInt(value.videoMaxWorkers);
      const audioMax = parsePositiveInt(value.audioMaxWorkers);
      if (imageMax === undefined || videoMax === undefined || audioMax === undefined) fail(t("max_workers_invalid"));
      const multiplier = parseOptionalMultiplier(value.providerMultiplier);
      if (multiplier === undefined) fail(t("cp_provider_multiplier_invalid"));

      const workers = { image_max_workers: imageMax ?? null, video_max_workers: videoMax ?? null, audio_max_workers: audioMax ?? null };
      if (existing) {
        // Update providers only, omitting models and retaining list/Sheet saved models.
        await API.fullUpdateCustomProvider(existing.id, {
          display_name: value.displayName,
          base_url: value.baseUrl,
          ...(value.noApiKey ? { api_key: "" } : value.apiKey ? { api_key: value.apiKey } : {}),
          ...workers,
        });
        // Provider multipliers are separate settings, written only when changed; failures after provider persistence report partial saves.
        let multiplierInfo = savedMultiplier;
        let multiplierError: unknown = null;
        if (value.providerMultiplier !== savedValue.providerMultiplier) {
          try {
            multiplierInfo = await API.setProviderMultiplier(customProviderKey(existing.id), multiplier ?? null);
          } catch (e) {
            multiplierError = e;
          }
        }
        // Refetched values define persisted state; refetch failure is not save failure.
        let updated: CustomProviderInfo | undefined;
        try {
          updated = await API.getCustomProvider(existing.id);
        } catch {
          updated = undefined;
        }
        // Return with providers so both saved values update in one render, without new-multiplier/old-provider intermediate states.
        setSavedMultiplier(multiplierInfo);
        onSaved(updated);
        const savedForm = updated
          ? formFromProvider(updated, multiplierInfo)
          : {
              ...value,
              apiKey: "",
              models,
              providerMultiplier: multiplierError ? savedValue.providerMultiplier : value.providerMultiplier,
            };
        if (multiplierError) {
          throw new PartialSaveError(t("cp_provider_multiplier_save_failed", { message: errMsg(multiplierError) }), {
            saved: savedForm,
            cause: multiplierError,
          });
        }
        return savedForm;
      }
      const created = await API.createCustomProvider({
        display_name: value.displayName,
        discovery_format: discoveryFormat,
        base_url: value.baseUrl,
        api_key: value.noApiKey ? "" : value.apiKey,
        models: payloadModels,
        ...workers,
      });
      useCapabilitiesStore.getState().invalidate();
      // Multipliers reference provider IDs and write only after creation. Failed multiplier writes never undo created providers;
      // notify then navigate normally to new providers, where users can reenter them.
      if (multiplier != null && multiplier !== newProviderDefaultMultiplier(isComfyuiProtocol(discoveryFormat))) {
        try {
          await API.setProviderMultiplier(customProviderKey(created.id), multiplier);
        } catch (e) {
          const message = t("cp_provider_multiplier_create_failed", { message: errMsg(e) });
          useAppStore.getState().pushToast(message, "error");
        }
      }
      onSaved(created);
    },
    [formatOf, deriveModels, t, existing, catalogInitialized, catalogEndpoints, initialEndpoint, savedMultiplier, onSaved],
  );

  const unit = useEditUnit({ source, save, allowNavigation });
  const value = unit.value;
  const setValue = unit.setValue;
  const discoveryFormat = formatOf(value);
  const isComfyui = isComfyuiProtocol(discoveryFormat);
  const models = useMemo(() => deriveModels(value), [deriveModels, value]);

  const setField = useCallback(
    <K extends keyof CustomProviderFormValue>(key: K, next: CustomProviderFormValue[K]) =>
      setValue((prev) => ({ ...prev, [key]: next })),
    [setValue],
  );

  // User edits write from derived rows, settling derived results. Otherwise fallback-reassigned endpoints and invalidated
  // default markers get rewritten by the same rule on later derivation, hiding user edits' effects.
  const updateModels = useCallback(
    (change: (rows: ModelRow[]) => ModelRow[]) =>
      setValue((prev) => ({ ...prev, models: change(deriveModels(prev)) })),
    [setValue, deriveModels],
  );

  // Expanded model rows exist only in creation forms; wired rows without IDs expand by default.
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set(source.models.map((m) => m.key)));
  const toggleExpanded = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const comfyuiEndpoints = useMemo(() => catalogEndpoints.filter(isComfyuiEndpoint), [catalogEndpoints]);
  // New ComfyUI rows require compatible endpoints; without any, disable buttons and show where to configure them.
  const noComfyuiEndpointYet = isComfyui && comfyuiEndpoints.length === 0;
  const addModel = () => {
    const endpoint = isComfyui ? comfyuiEndpoints[0]?.key : undefined;
    const row = endpoint ? newModelRow({ endpoint }) : newModelRow();
    updateModels((rows) => [...rows, row]);
    setExpanded((prev) => new Set(prev).add(row.key));
  };

  // --- Test connection/fetch models: current form contents, without saving ---
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const showError = useCallback((msg: string) => useAppStore.getState().pushToast(msg, "error"), []);

  // Changed base_url versus stored values requires UI addresses/new secrets through plaintext paths,
  // or by-id endpoints use old DB base_url, diverging from newly saved addresses.
  const baseUrlChanged = !!existing && value.baseUrl.trim() !== existing.base_url.trim();
  // Unchanged API addresses without newly entered secrets use stored credentials (by-id); creation/address changes require plaintext
  // secrets. No key required saves empty keys, so tests/discovery must likewise use plaintext empty-key paths.
  const useStoredCredential = !!existing && !value.apiKey && !baseUrlChanged && !value.noApiKey;
  // ComfyUI itself has no auth; proxy credential templates live in definition auth (docs/adr/0081), so empty secrets are normal.
  const keyOptional = value.noApiKey || isComfyui;

  /** Connection-test/model-fetch preflight: notify and return false when unmet. */
  const checkConnectionInputs = () => {
    if (!value.baseUrl) {
      showError(t("fill_base_url_first"));
      return false;
    }
    if (!useStoredCredential && !keyOptional && !value.apiKey) {
      showError(t(baseUrlChanged ? "base_url_changed_reenter_key" : "fill_api_key_first"));
      return false;
    }
    return true;
  };

  // Model fetch connection details freeze when dialogs open; later form edits never refetch their lists.
  const [fetchConnection, setFetchConnection] = useState<{
    discovery_format: DiscoveryFormat;
    base_url: string;
    api_key: string;
  } | null>(null);
  const openFetch = () => {
    if (!checkConnectionInputs()) return;
    setFetchConnection({ discovery_format: discoveryFormat, base_url: value.baseUrl, api_key: value.apiKey });
  };
  const loadFetched = useCallback(
    async (signal: AbortSignal): Promise<FetchProviderModelsResponse> => {
      if (!fetchConnection) return { models: [], not_applicable: false, reason: null };
      const res = await API.discoverModels(fetchConnection, { signal });
      // Read latest catalog mappings through getState: async mount fetches may remain pending when dialogs open.
      const mediaMap = useEndpointCatalogStore.getState().endpointToMediaType;
      return { ...res, models: res.models.map((m) => discoveredToFetched(m, (endpoint) => mediaMap[endpoint])) };
    },
    [fetchConnection],
  );
  const isFetchedAdded = useCallback(
    (fetched: FetchedProviderModel) => models.some((m) => m.model_id.trim() === fetched.model_id),
    [models],
  );
  const addFetched = useCallback(
    (selections: FetchSelection[]) => {
      const { endpointToMediaType: mediaMap, endpointToImageCapabilities: capsMap } =
        useEndpointCatalogStore.getState();
      // First checked model per media type defaults; merging yields when existing-row defaults conflict.
      const seenMedia = new Set<string>();
      const rows = selections.map((selection) => {
        const media = selection.endpoint ? mediaMap[selection.endpoint] : undefined;
        const isDefault = media !== undefined && !seenMedia.has(media);
        if (media !== undefined) seenMedia.add(media);
        return fetchedToRow(selection, isDefault);
      });
      updateModels((prev) => mergeDiscoveredModels(prev, rows, mediaMap, capsMap));
      return Promise.resolve(selections.map(() => null));
    },
    [updateModels],
  );

  const handleTest = async () => {
    // Clear prior results first; failed validation returning early must retain no old success/failure notices.
    setTestResult(null);
    if (!checkConnectionInputs()) return;
    setTesting(true);
    try {
      const res =
        useStoredCredential && existing
          ? await API.checkCustomConnectivityById(existing.id)
          : await API.checkCustomConnectivity({
              discovery_format: discoveryFormat,
              base_url: value.baseUrl,
              api_key: value.apiKey,
            });
      setTestResult(res);
    } catch (e) {
      setTestResult({ success: false, message: errMsg(e, t("connectivity_check_failed")) });
    } finally {
      setTesting(false);
    }
  };

  // Only No key required needs endpoint credential requirements, so fetch custom endpoint lists once then.
  // Built-ins always require keys: their definitions contain auth; credential-free APIs can only be user-created.
  // null means lists have not loaded successfully; skip conflict hints, since empty Sets falsely flag every model as needing keys.
  const [authFreeEndpoints, setAuthFreeEndpoints] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (!value.noApiKey) return;
    const controller = new AbortController();
    voidCall(
      API.listCustomEndpoints({ signal: controller.signal })
        .then((res) => {
          if (controller.signal.aborted) return;
          setAuthFreeEndpoints(
            new Set(
              res.endpoints
                .filter((e) => {
                  const auth = e.definition?.auth;
                  return Object.keys(auth?.headers ?? {}).length === 0 && Object.keys(auth?.query ?? {}).length === 0;
                })
                .map((e) => e.key),
            ),
          );
        })
        .catch(() => {
          // Failed reads show no hints; supplementary guidance is better absent than incorrect.
        }),
    );
    return () => controller.abort();
  }, [value.noApiKey]);
  const keyRequiringModels =
    authFreeEndpoints === null
      ? []
      : (existing?.models ?? models)
          .filter((m) => m.is_enabled && m.model_id.trim() && !authFreeEndpoints.has(m.endpoint))
          .map((m) => m.model_id);

  // Refetch providers after model Sheet saves/removal; delete-confirmation model counts and credential-conflict hints depend on them.
  const refreshAfterModelChange = useCallback(() => {
    if (!existing) return;
    voidCall(
      API.getCustomProvider(existing.id)
        .then((updated) => onSaved(updated))
        .catch(() => onSaved()),
    );
  }, [existing, onSaved]);

  // --- Delete provider ---
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const handleDelete = async () => {
    if (!existing) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await API.deleteCustomProvider(existing.id);
      // Provider models/overrides disappear together, making projects resolve other models without changing any project fields.
      useCapabilitiesStore.getState().invalidate();
      deletedRef.current = true;
      setConfirmDelete(false);
      onDeleted?.();
    } catch (e) {
      setDeleteError(errMsg(e));
    } finally {
      setDeleting(false);
    }
  };

  const [showApiKey, setShowApiKey] = useState(false);
  const nameId = useId();
  const urlId = useId();
  const keyId = useId();
  const formatId = useId();
  const modelsHeadingId = useId();
  const connectionHeadingId = useId();
  const concurrencyHeadingId = useId();
  const billingHeadingId = useId();
  const providerMultiplierInvalid = parseOptionalMultiplier(value.providerMultiplier) === undefined;
  const urlPreview = urlPreviewFor(discoveryFormat, value.baseUrl);
  const hasUnsetEndpoint = models.some((m) => !m.endpoint);
  const workersPlaceholder = isComfyui ? t("cp_max_workers_placeholder_comfyui") : t("cp_max_workers_placeholder");
  const protocolName = t(DISCOVERY_FORMAT_OPTIONS.find((o) => o.value === discoveryFormat)?.labelKey ?? "");

  const header = (
    <div className="flex items-start gap-3">
      {/* Never infer custom-provider brand icons from names; relay sites are protocol-independent, so use initials. */}
      <span
        aria-hidden
        className="mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-sm text-subtle-foreground"
      >
        {existing ? (Array.from(existing.display_name)[0] ?? "?") : <Plus className="size-4" />}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <h2 className="min-w-0 text-lg font-medium">
          {existing ? <TruncatedText text={existing.display_name} /> : t("add_custom_provider_title")}
        </h2>
        {existing && (
          <p className="flex min-w-0 items-center gap-2 text-sm text-muted-foreground">
            <span className="shrink-0">{protocolName}</span>
            <span aria-hidden>·</span>
            <TruncatedText text={existing.base_url} className="font-mono" />
          </p>
        )}
      </div>
      <Button variant="outline" size="sm" disabled={testing} onClick={() => void handleTest()}>
        {testing && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
        {testing ? t("connectivity_checking") : t("connectivity_check")}
      </Button>
      {existing && (
        <DropdownMenu>
          <DropdownMenuTrigger
            render={<Button variant="ghost" size="icon-sm" aria-label={t("cp_more_actions")} />}
          >
            <MoreHorizontal />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              variant="destructive"
              onClick={() => {
                setDeleteError(null);
                setConfirmDelete(true);
              }}
            >
              <Trash2 />
              {t("cp_delete_provider")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );

  return (
    <DetailPane header={header} footer={<SaveBar unit={unit} className="max-w-6xl" />}>
      <div className="flex flex-col gap-10 px-6 py-6">
        <section aria-labelledby={connectionHeadingId} className="flex max-w-178 flex-col gap-4">
          <h3 id={connectionHeadingId} className="text-base font-medium">
            {t("cp_connection_heading")}
          </h3>
          <FieldRow label={t("cp_name_label")} htmlFor={nameId}>
            <Input
              id={nameId}
              autoComplete="off"
              value={value.displayName}
              onChange={(e) => setField("displayName", e.target.value)}
              placeholder={t("cp_name_placeholder")}
            />
          </FieldRow>
          <FieldRow
            label={t("discovery_format_label")}
            htmlFor={formatId}
            hint={<FieldHint>{existing ? t("cp_protocol_locked_hint") : t("discovery_format_help")}</FieldHint>}
          >
            <Select<DiscoveryFormat>
              value={discoveryFormat}
              onValueChange={(next) => next && setField("pickedFormat", next)}
              disabled={!!existing}
              items={DISCOVERY_FORMAT_OPTIONS.map((o) => ({ value: o.value, label: t(o.labelKey) }))}
            >
              <SelectTrigger id={formatId} className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DISCOVERY_FORMAT_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {t(o.labelKey)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FieldRow>
          <FieldRow
            label={t("cp_base_url_label")}
            htmlFor={urlId}
            hint={
              urlPreview ? (
                <FieldHint>
                  {t("preview_url")}
                  <span className="font-mono break-all">{urlPreview}</span>
                </FieldHint>
              ) : undefined
            }
          >
            <Input
              id={urlId}
              type="url"
              autoComplete="off"
              value={value.baseUrl}
              onChange={(e) => setField("baseUrl", e.target.value)}
              placeholder="https://api.example.com"
            />
          </FieldRow>
          <FieldRow
            label={t("credential_secret_label")}
            htmlFor={value.noApiKey ? undefined : keyId}
            hint={
              value.noApiKey && keyRequiringModels.length > 0 ? (
                <FieldHint tone="warn">
                  {t("cp_no_api_key_conflict", { models: formatNameList(keyRequiringModels, i18n.language) })}
                </FieldHint>
              ) : undefined
            }
          >
            {!value.noApiKey && (
              <InputGroup>
                <InputGroupInput
                  mono
                  id={keyId}
                  type={showApiKey ? "text" : "password"}
                  autoComplete="off"
                  value={value.apiKey}
                  onChange={(e) => setField("apiKey", e.target.value)}
                  placeholder={
                    existing ? existing.api_key_masked || t("keep_existing_key_hint") : t("enter_api_key_placeholder")
                  }
                />
                <InputGroupAddon align="inline-end">
                  <InputGroupButton
                    size="icon-xs"
                    aria-label={showApiKey ? t("common:hide") : t("common:show")}
                    onClick={() => setShowApiKey((v) => !v)}
                  >
                    {showApiKey ? <EyeOff aria-hidden /> : <Eye aria-hidden />}
                  </InputGroupButton>
                </InputGroupAddon>
              </InputGroup>
            )}
            <label className="flex items-center gap-2 text-sm text-subtle-foreground">
              <Checkbox
                checked={value.noApiKey}
                onCheckedChange={(checked) =>
                  setValue((prev) => ({ ...prev, noApiKey: checked, apiKey: checked ? "" : prev.apiKey }))
                }
              />
              {t("cp_no_api_key")}
            </label>
          </FieldRow>
          {/* Proxy custom-header auth may block ComfyUI probes; explain this criterion in advance. */}
          {isComfyui && <p className="text-sm text-muted-foreground">{t("cp_comfyui_connectivity_hint")}</p>}
          {testResult && (
            <Alert variant={testResult.success ? "default" : "destructive"}>
              {testResult.success ? <CheckCircle2 aria-hidden /> : <XCircle aria-hidden />}
              <AlertDescription>{testResult.message}</AlertDescription>
            </Alert>
          )}
        </section>

        {/* Provider settings precede model lists; page save bars save API addresses/multipliers. */}
        <section aria-labelledby={billingHeadingId} className="flex max-w-178 flex-col gap-4">
          <div className="flex flex-col gap-1">
            <h3 id={billingHeadingId} className="text-base font-medium">
              {t("cp_billing_heading")}
            </h3>
            <p className="text-sm text-muted-foreground">{t("cp_provider_multiplier_help")}</p>
          </div>
          <FieldRow
            label={t("cp_provider_multiplier_label")}
            htmlFor={`${billingHeadingId}-input`}
            hint={
              providerMultiplierInvalid ? (
                <FieldHint tone="warn">{t("cp_multiplier_invalid")}</FieldHint>
              ) : undefined
            }
          >
            <Input
              id={`${billingHeadingId}-input`}
              inputMode="decimal"
              autoComplete="off"
              className="w-32"
              value={value.providerMultiplier}
              onChange={(e) => setField("providerMultiplier", e.target.value)}
              placeholder={t("cp_provider_multiplier_placeholder", {
                value: existing ? savedMultiplier.default : newProviderDefaultMultiplier(isComfyui),
              })}
              aria-invalid={providerMultiplierInvalid}
            />
          </FieldRow>
        </section>

        {existing ? (
          <div className="max-w-178">
            <ProviderModelList
              providerId={customProviderKey(existing.id)}
              providerName={existing.display_name}
              custom={{ id: existing.id, baseUrl: existing.base_url, protocol: existing.discovery_format }}
              initialModelId={focusModelId}
              onModelsChanged={refreshAfterModelChange}
              providerMultiplier={savedMultiplier.multiplier}
            />
          </div>
        ) : (
          <section aria-labelledby={modelsHeadingId} className="flex max-w-6xl flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <h3 id={modelsHeadingId} className="mr-auto text-base font-medium">
                {t("model_list")}
                {models.length > 0 && <span className="ml-2 text-muted-foreground tabular-nums">{models.length}</span>}
              </h3>
              {!isComfyui && (
                <Button variant="outline" size="sm" onClick={openFetch}>
                  <CloudDownload aria-hidden data-icon="inline-start" />
                  {t("pm_fetch_models")}
                </Button>
              )}
              <Button variant="outline" size="sm" disabled={noComfyuiEndpointYet} onClick={addModel}>
                <Plus aria-hidden data-icon="inline-start" />
                {t("add_model_manually")}
              </Button>
            </div>
            {/* comfyui lacks model discovery; explanatory text replaces buttons. */}
            {isComfyui && <p className="text-sm text-muted-foreground">{t("discovery_not_applicable")}</p>}
            {hasUnsetEndpoint && <p className="text-sm text-warn">{t("cp_model_endpoint_unselected")}</p>}
            {models.length > 0 ? (
              <ModelTable
                rows={models}
                protocol={discoveryFormat}
                baseUrl={value.baseUrl}
                expanded={expanded}
                onToggleExpanded={toggleExpanded}
                onUpdate={(key, patch) =>
                  updateModels((rows) => rows.map((m) => (m.key === key ? { ...m, ...patch } : m)))
                }
                onRemove={(key) => updateModels((rows) => rows.filter((m) => m.key !== key))}
                onToggleDefault={(key) =>
                  updateModels((rows) => toggleDefaultReducer(rows, key, endpointToMediaType, endpointToImageCapabilities))
                }
                onSetEnabled={(keys, enabled) => {
                  const selected = new Set(keys);
                  updateModels((rows) => rows.map((m) => (selected.has(m.key) ? { ...m, is_enabled: enabled } : m)));
                }}
              />
            ) : (
              <p className="rounded-lg border border-dashed border-input px-4 py-6 text-center text-sm text-muted-foreground">
                {noComfyuiEndpointYet
                  ? t("cp_comfyui_no_endpoint_hint")
                  : isComfyui
                    ? t("cp_comfyui_add_model_hint")
                    : t("cp_models_empty_hint")}
              </p>
            )}
            <FetchModelsDialog
              open={fetchConnection !== null}
              onOpenChange={(open) => {
                if (!open) setFetchConnection(null);
              }}
              providerName={value.displayName.trim() || t("add_custom_provider_title")}
              load={loadFetched}
              isAdded={isFetchedAdded}
              protocol={discoveryFormat}
              onAdd={addFetched}
            />
          </section>
        )}

        <section aria-labelledby={concurrencyHeadingId} className="flex max-w-178 flex-col gap-4">
          <div className="flex flex-col gap-1">
            <h3 id={concurrencyHeadingId} className="text-base font-medium">
              {t("cp_concurrency_label")}
            </h3>
            <p className="text-sm text-muted-foreground">
              {isComfyui ? t("cp_concurrency_help_comfyui") : t("cp_concurrency_help")}
            </p>
          </div>
          {(
            [
              ["imageMaxWorkers", "cp_image_max_workers_label"],
              ["videoMaxWorkers", "cp_video_max_workers_label"],
              ["audioMaxWorkers", "cp_audio_max_workers_label"],
            ] as const
          ).map(([field, labelKey]) => (
            <FieldRow key={field} label={t(labelKey)} htmlFor={`${concurrencyHeadingId}-${field}`}>
              <Input
                id={`${concurrencyHeadingId}-${field}`}
                type="number"
                min={1}
                step={1}
                inputMode="numeric"
                autoComplete="off"
                className="w-32"
                value={value[field]}
                onChange={(e) => setField(field, e.target.value)}
                placeholder={workersPlaceholder}
              />
            </FieldRow>
          ))}
        </section>
      </div>

      {existing && (
        <AlertDialog
          open={confirmDelete}
          onOpenChange={(open) => {
            // In-flight deletion ignores Esc.
            if (!open && !deleting) setConfirmDelete(false);
          }}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t("cp_delete_provider_title", { name: existing.display_name })}</AlertDialogTitle>
            </AlertDialogHeader>
            <AlertDialogBody tabIndex={0} role="region" aria-label={t("cp_delete_provider_title", { name: existing.display_name })}>
              <div className="flex flex-col gap-2">
                <AlertDialogDescription>
                  {t("cp_delete_provider_description", { count: existing.models.length })}
                </AlertDialogDescription>
                {deleteError && (
                  <p role="alert" className="text-sm wrap-break-word text-destructive">
                    {t("delete_failed", { message: deleteError })}
                  </p>
                )}
              </div>
            </AlertDialogBody>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={deleting}>{t("common:cancel")}</AlertDialogCancel>
              <AlertDialogAction variant="destructive" disabled={deleting} onClick={() => void handleDelete()}>
                {deleting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
                {t("cp_delete_provider")}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </DetailPane>
  );
}

