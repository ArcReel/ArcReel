import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { ExternalLink, Loader2, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import { API } from "@/api";
import { endpointSettingsPath } from "@/app-routes";
import { useConfirmLeave } from "@/components/shared/edit-unit/LeaveGuard";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { ResolutionPicker } from "@/components/shared/ResolutionPicker";
import { TruncatedText } from "@/components/shared/TruncatedText";
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
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Sheet, SheetBody, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type {
  CapabilityOverrides,
  DiscoveryFormat,
  PathOverrides,
  ProviderModelDetail,
  ProviderModelItem,
  ProviderModelReferences,
  ProviderModelSettingsUpdate,
} from "@/types";
import { errMsg, voidCall } from "@/utils/async";
import { parseMultiplier } from "@/utils/multiplier";
import { IMAGE_STANDARD_RESOLUTIONS, VIDEO_STANDARD_RESOLUTIONS, resolutionPlaceholder } from "@/utils/provider-models";
import { FieldHint, FieldRow } from "./CustomProviderFieldRow";
import { EndpointSelect } from "./EndpointSelect";
import { ProviderCapabilityOverrides } from "./ProviderCapabilityOverrides";
import { priceForSave, samePrice } from "./price-editor/priceTable";
import { ENTRY_INVALID_PREFIX, ProviderModelEntryInfo, entryDraftOf, providerNames, type EntryDraft } from "./ProviderModelEntryInfo";
import { ProviderModelFlags } from "./providerModelDisplay";
import { RequestAddresses } from "./RequestAddresses";

/** 自定义供应商的模型另有调用端点与请求地址，需要供应商的接口地址与模型发现协议。 */
export interface CustomProviderContext {
  /** 自定义供应商的数据库 id：「打开调用端点」据此带上返回地址。 */
  id: number;
  baseUrl: string;
  protocol: DiscoveryFormat;
}

/** 打开的模型；`key` 每次打开都不同，同一个模型关掉再打开时重新读取。 */
export interface OpenProviderModel {
  modelId: string;
  key: number;
}

interface Props {
  /** 内置供应商 ID，或自定义供应商的 `custom-<id>`。 */
  providerId: string;
  providerName: string;
  /** 为 null 时 Sheet 关闭。 */
  open: OpenProviderModel | null;
  custom?: CustomProviderContext;
  onClose: () => void;
  /** 保存之后带回保存后的列表行；改了模型 ID 时 `previousModelId` 是原来的 ID。 */
  onSaved: (model: ProviderModelItem, previousModelId: string) => void;
  onRemoved: (modelId: string) => void;
}

/**
 * 供应商模型 Sheet：上段「供应商设置」只属于这个供应商（启用、默认、模型 ID、倍率、这个供应商支持的参数、
 * 调用端点与请求地址），官方收录的模型只有启用；下段「模型信息」是对应到的模型条目，所有使用它的供应商共用。
 * 两段共用一份草稿与一条保存栏，在同一个请求里保存。
 */
export function ProviderModelSheet({ providerId, providerName, open, custom, onClose, onSaved, onRemoved }: Props) {
  // 关闭动画期间仍显示刚关掉的模型，动画结束后卸载
  const [shown, setShown] = useState(open);
  if (open !== null && open !== shown) setShown(open);
  const requestCloseRef = useRef<() => void>(onClose);

  return (
    <Sheet
      open={open !== null}
      onOpenChange={(next) => {
        if (!next) requestCloseRef.current();
      }}
      onOpenChangeComplete={(next) => {
        if (!next) setShown(null);
      }}
    >
      <SheetContent side="right" className="data-[side=right]:w-180">
        {shown && (
          <ModelSheetLoader
            key={shown.key}
            providerId={providerId}
            providerName={providerName}
            modelId={shown.modelId}
            custom={custom}
            requestCloseRef={requestCloseRef}
            onClose={onClose}
            onSaved={onSaved}
            onRemoved={onRemoved}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}

interface LoaderProps extends Omit<Props, "open"> {
  modelId: string;
  requestCloseRef: RefObject<() => void>;
}

function ModelSheetLoader({ modelId, providerId, providerName, requestCloseRef, onClose, ...rest }: LoaderProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [detail, setDetail] = useState<ProviderModelDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    voidCall(
      API.getProviderModel(providerId, modelId, { signal: controller.signal })
        .then((res) => {
          if (controller.signal.aborted) return;
          setDetail(res);
          setLoadError(null);
        })
        .catch((err: unknown) => {
          if (!controller.signal.aborted) setLoadError(errMsg(err));
        }),
    );
    return () => controller.abort();
  }, [providerId, modelId, reloadKey]);

  // 读取完成前没有可丢的修改，直接关闭
  useEffect(() => {
    if (detail) return;
    requestCloseRef.current = onClose;
  }, [detail, onClose, requestCloseRef]);

  if (!detail) {
    return (
      <>
        <SheetHeader>
          <SheetTitle>
            <TruncatedText text={modelId} />
          </SheetTitle>
          <SheetDescription>{providerName}</SheetDescription>
        </SheetHeader>
        <SheetBody>
          {loadError ? (
            <div role="alert" className="flex flex-col items-start gap-2">
              <p className="text-sm text-warn">{t("pm_sheet_load_failed", { message: loadError })}</p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setLoadError(null);
                  setReloadKey((k) => k + 1);
                }}
              >
                {t("common:retry")}
              </Button>
            </div>
          ) : (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
              {t("common:loading")}
            </p>
          )}
        </SheetBody>
      </>
    );
  }

  return (
    <ModelSheetForm
      detail={detail}
      onDetailChange={setDetail}
      providerId={providerId}
      providerName={providerName}
      requestCloseRef={requestCloseRef}
      onClose={onClose}
      {...rest}
    />
  );
}

/** Sheet 的草稿：上段的供应商设置与下段的模型信息。倍率保留输入原文，保存时才解析。 */
interface Draft {
  enabled: boolean;
  isDefault: boolean;
  modelId: string;
  multiplierText: string;
  overrides: CapabilityOverrides;
  endpoint: string;
  pathOverrides: PathOverrides | null;
  resolution: string;
  entry: EntryDraft | null;
}

function draftOf(detail: ProviderModelDetail): Draft {
  return {
    enabled: detail.model.enabled,
    isDefault: detail.model.is_default,
    modelId: detail.model.model_id,
    multiplierText: String(detail.multiplier ?? 1),
    overrides: detail.capabilities.overrides,
    endpoint: detail.endpoint ?? "",
    pathOverrides: detail.path_overrides,
    resolution: detail.resolution ?? "",
    entry: entryDraftOf(detail.entry),
  };
}

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** 草稿是否相同：模型信息按 {@link sameEntryDraft} 比，其余字段按 JSON。 */
function sameDraft(a: Draft, b: Draft): boolean {
  return sameJson({ ...a, entry: null }, { ...b, entry: null }) && sameEntryDraft(a.entry, b.entry);
}

/** 模型信息的草稿是否相同；没有计费项的价目表与没有价目表等价，只换了币种不算修改。 */
function sameEntryDraft(a: EntryDraft | null, b: EntryDraft | null): boolean {
  if (a === null || b === null) return a === b;
  return a.display_name === b.display_name && sameJson(a.facts, b.facts) && samePrice(a.price, b.price);
}

/** 两级倍率相乘后的最终倍率，去掉浮点尾差。 */
function finalMultiplier(provider: number, model: number): string {
  return String(Number((provider * model).toFixed(6)));
}

interface FormProps extends Omit<LoaderProps, "modelId"> {
  detail: ProviderModelDetail;
  onDetailChange: (detail: ProviderModelDetail) => void;
}

function ModelSheetForm({
  detail,
  onDetailChange,
  providerId,
  providerName,
  custom,
  requestCloseRef,
  onClose,
  onSaved,
  onRemoved,
}: FormProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const official = detail.model.official;
  const userOwned = !official;
  const isCustom = custom !== undefined && detail.endpoint !== null;
  const [invalidKeys, setInvalidKeys] = useState<ReadonlySet<string>>(new Set());
  const setValidity = useCallback((key: string, valid: boolean) => {
    setInvalidKeys((prev) => {
      if (prev.has(key) === !valid) return prev;
      const next = new Set(prev);
      if (valid) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);
  const entryInvalid = [...invalidKeys].some((key) => key.startsWith(ENTRY_INVALID_PREFIX));
  const providerInvalid = [...invalidKeys].some((key) => !key.startsWith(ENTRY_INVALID_PREFIX));

  const fetchEndpointCatalog = useEndpointCatalogStore((s) => s.fetch);
  useEffect(() => {
    if (isCustom) voidCall(fetchEndpointCatalog());
  }, [isCustom, fetchEndpointCatalog]);

  const source = useMemo(() => draftOf(detail), [detail]);
  const save = useCallback(
    async (value: Draft, saved: Draft): Promise<Draft> => {
      const patch: ProviderModelSettingsUpdate = {};
      if (value.enabled !== saved.enabled) patch.enabled = value.enabled;
      if (isCustom && value.isDefault !== saved.isDefault) patch.is_default = value.isDefault;
      if (userOwned) {
        const modelId = value.modelId.trim();
        if (!modelId) throw new Error(t("pm_sheet_model_id_required"));
        if (modelId !== saved.modelId) patch.model_id = modelId;
        if (value.multiplierText !== saved.multiplierText) {
          const multiplier = parseMultiplier(value.multiplierText);
          if (multiplier === undefined) throw new Error(t("cp_multiplier_invalid"));
          patch.multiplier = multiplier;
        }
        if (providerInvalid) throw new Error(t("pm_sheet_capabilities_invalid"));
        if (!sameJson(value.overrides, saved.overrides)) {
          patch.capability_overrides = Object.keys(value.overrides).length > 0 ? value.overrides : null;
        }
      }
      if (isCustom) {
        if (!value.endpoint) throw new Error(t("cp_model_endpoint_unselected"));
        if (value.endpoint !== saved.endpoint) patch.endpoint = value.endpoint;
        if (!sameJson(value.pathOverrides, saved.pathOverrides)) patch.path_overrides = value.pathOverrides;
        if (value.resolution !== saved.resolution) patch.resolution = value.resolution || null;
      }
      const entry = detail.entry;
      if (entry && value.entry && saved.entry && !sameEntryDraft(value.entry, saved.entry)) {
        if (entryInvalid || !value.entry.display_name.trim()) throw new Error(t("pm_sheet_entry_invalid"));
        const facts = Object.fromEntries(
          Object.entries(value.entry.facts).filter(([key, fact]) => !sameJson(fact, saved.entry?.facts[key])),
        );
        patch.entry = { maker: entry.maker, name: entry.name, media_type: entry.media_type };
        if (value.entry.display_name !== saved.entry.display_name) patch.entry.display_name = value.entry.display_name.trim();
        if (Object.keys(facts).length > 0) patch.entry.facts = facts;
        if (!samePrice(value.entry.price, saved.entry.price)) patch.entry.price = priceForSave(value.entry.price);
      }
      const updated = await API.updateProviderModel(providerId, detail.model.model_id, patch);
      // 能力覆盖不落任何项目字段，在用的能力查询不会因此重取：显式作废
      useCapabilitiesStore.getState().invalidate();
      onDetailChange(updated);
      onSaved(updated.model, detail.model.model_id);
      return draftOf(updated);
    },
    [isCustom, userOwned, providerInvalid, entryInvalid, providerId, detail.model.model_id, detail.entry, onDetailChange, onSaved, t],
  );
  const unit = useEditUnit({ source, save, isEqual: sameDraft, leaveTitle: t("pm_sheet_leave_title", { name: detail.model.display_name ?? detail.model.model_id }) });
  const draft = unit.value;
  const setDraft = (patch: Partial<Draft>) => unit.setValue((prev) => ({ ...prev, ...patch }));

  // 关闭 Sheet：有未保存修改或保存在途时交给离开拦截；否则直接关，不连带询问页面上其他编辑单元
  const confirmLeave = useConfirmLeave();
  const busy = unit.dirty || unit.status === "saving";
  useEffect(() => {
    requestCloseRef.current = busy ? () => confirmLeave(onClose) : onClose;
  }, [busy, confirmLeave, onClose, requestCloseRef]);

  const endpointChanged = isCustom && draft.endpoint !== detail.endpoint;
  const endpointMedia = useEndpointCatalogStore((s) => s.endpointToMediaType[draft.endpoint]);
  const endpointConstraints = useEndpointCatalogStore((s) => s.endpointConstraints[draft.endpoint]);
  const endpointRequests = useEndpointCatalogStore((s) => s.endpoints.find((e) => e.key === draft.endpoint)?.requests);
  const requests = endpointChanged ? (endpointRequests ?? []) : detail.requests;
  const media = isCustom ? (endpointMedia ?? detail.model.media_type) : detail.model.media_type;

  const multiplier = parseMultiplier(draft.multiplierText);
  const showCapabilities = userOwned && detail.capabilities.editable.length > 0;

  const idPrefix = useId();
  const name = detail.model.display_name ?? detail.model.model_id;
  // 「已修改」在下段的维护说明里讲，标题旁只留影响调用的标记
  const titleFlags = detail.model.flags.filter((flag) => flag !== "modified");
  const entryDirty = !sameEntryDraft(draft.entry, unit.savedValue.entry);
  const stopsFollowing = entryDirty && detail.entry?.source === "official";

  // 恢复官方数据立即生效、不经保存栏：重读 Sheet 内容，只把下段的草稿换成官方值，上段未保存的修改保留
  const handleRestored = useCallback(async () => {
    const updated = await API.getProviderModel(providerId, detail.model.model_id);
    useCapabilitiesStore.getState().invalidate();
    onDetailChange(updated);
    unit.setValue((prev) => ({ ...prev, entry: entryDraftOf(updated.entry) }));
    onSaved(updated.model, detail.model.model_id);
  }, [providerId, detail.model.model_id, onDetailChange, onSaved, unit]);

  return (
    <>
      <SheetHeader>
        <SheetTitle>
          <span className="flex min-w-0 flex-wrap items-center gap-2">
            <TruncatedText text={name} />
            <ProviderModelFlags flags={titleFlags} />
          </span>
        </SheetTitle>
        <SheetDescription>{providerName}</SheetDescription>
      </SheetHeader>

      <SheetBody>
        <section aria-labelledby={`${idPrefix}-settings`} className="flex flex-col gap-4 pt-1 pb-5">
          <h3 id={`${idPrefix}-settings`} className="text-base font-medium">
            {t("pm_sheet_provider_settings")}
          </h3>

          <div className="flex flex-col gap-1.5">
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
              <label className="flex items-center gap-2 text-sm">
                <Switch
                  checked={draft.enabled}
                  onCheckedChange={(enabled) => setDraft({ enabled, ...(enabled ? {} : { isDefault: false }) })}
                />
                {t("pm_sheet_enabled")}
              </label>
              {isCustom && (
                <label className="flex items-center gap-2 text-sm">
                  <Switch
                    checked={draft.isDefault}
                    onCheckedChange={(isDefault) => setDraft({ isDefault, ...(isDefault ? { enabled: true } : {}) })}
                  />
                  {t("pm_sheet_default")}
                </label>
              )}
            </div>
            {official && <p className="text-xs text-muted-foreground">{t("pm_sheet_official_note")}</p>}
          </div>

          {userOwned && (
            <FieldRow
              label={t("model_id_label")}
              htmlFor={`${idPrefix}-model-id`}
              hint={
                draft.modelId.trim() !== detail.model.model_id ? (
                  <FieldHint tone="warn">{t("pm_sheet_model_id_renamed")}</FieldHint>
                ) : (
                  <FieldHint>{t("pm_sheet_model_id_hint")}</FieldHint>
                )
              }
            >
              <Input
                mono
                id={`${idPrefix}-model-id`}
                autoComplete="off"
                spellCheck={false}
                value={draft.modelId}
                aria-invalid={!draft.modelId.trim()}
                onChange={(e) => setDraft({ modelId: e.target.value })}
              />
            </FieldRow>
          )}

          {isCustom && custom && (
            <FieldRow
              label={t("endpoint_label")}
              htmlFor={`${idPrefix}-endpoint`}
              hint={
                endpointChanged && detail.path_overrides ? (
                  <FieldHint tone="warn">{t("cp_endpoint_change_resets_paths")}</FieldHint>
                ) : undefined
              }
            >
              <EndpointSelect
                id={`${idPrefix}-endpoint`}
                value={draft.endpoint}
                protocol={custom.protocol}
                onChange={(endpoint) =>
                  unit.setValue((prev) => {
                    // 换回原来的端点时取回保存的请求地址与参数修改；换到别的端点时两者都作废
                    const back = endpoint === detail.endpoint;
                    return {
                      ...prev,
                      endpoint,
                      pathOverrides: back ? source.pathOverrides : null,
                      overrides: back ? source.overrides : {},
                      resolution: back ? source.resolution : "",
                    };
                  })
                }
              />
              {draft.endpoint && (
                <Link
                  href={endpointSettingsPath(draft.endpoint, { fromCustomProvider: custom.id })}
                  className={buttonVariants({ variant: "link", size: "sm", className: "self-start px-0" })}
                >
                  <ExternalLink aria-hidden data-icon="inline-start" />
                  {t("cp_open_endpoint")}
                </Link>
              )}
            </FieldRow>
          )}

          {userOwned && (
            <FieldRow
              label={t("pm_multiplier_label")}
              htmlFor={`${idPrefix}-multiplier`}
              hint={
                multiplier === undefined ? (
                  <FieldHint tone="warn">{t("cp_multiplier_invalid")}</FieldHint>
                ) : detail.provider_multiplier !== 1 ? (
                  <FieldHint>
                    {t("pm_sheet_multiplier_final", {
                      provider: detail.provider_multiplier,
                      final: finalMultiplier(detail.provider_multiplier, multiplier),
                    })}
                  </FieldHint>
                ) : undefined
              }
            >
              <Input
                id={`${idPrefix}-multiplier`}
                inputMode="decimal"
                autoComplete="off"
                className="w-24"
                value={draft.multiplierText}
                aria-invalid={multiplier === undefined}
                onChange={(e) => setDraft({ multiplierText: e.target.value })}
              />
            </FieldRow>
          )}

          {isCustom && (media === "image" || media === "video") && (
            <FieldRow
              label={t("resolution_label")}
              hint={endpointConstraints?.sizeFixed ? <FieldHint>{t("resolution_fixed_hint")}</FieldHint> : undefined}
            >
              <ResolutionPicker
                mode="combobox"
                options={media === "image" ? IMAGE_STANDARD_RESOLUTIONS : VIDEO_STANDARD_RESOLUTIONS}
                value={draft.resolution || null}
                onChange={(v) => setDraft({ resolution: v ?? "" })}
                placeholder={resolutionPlaceholder(endpointConstraints, t)}
                aria-label={t("resolution_label")}
                disabled={endpointConstraints?.sizeFixed ?? false}
              />
            </FieldRow>
          )}

          {showCapabilities && (
            <Subsection
              title={t("pm_sheet_capabilities")}
              hint={endpointChanged ? t("pm_sheet_capabilities_endpoint_changed") : t("pm_sheet_capabilities_hint")}
            >
              {!endpointChanged && (
                <ProviderCapabilityOverrides
                  capabilities={detail.capabilities}
                  value={draft.overrides}
                  onChange={(overrides) => setDraft({ overrides })}
                  onValidityChange={setValidity}
                />
              )}
            </Subsection>
          )}

          {isCustom && custom && requests.length > 0 && (
            <Subsection title={t("cp_request_address")} hint={t("pm_sheet_request_address_hint")}>
              <RequestAddresses
                requests={requests}
                baseUrl={custom.baseUrl}
                modelId={draft.modelId}
                overrides={draft.pathOverrides}
                onChange={(pathOverrides) => setDraft({ pathOverrides })}
              />
            </Subsection>
          )}

          {userOwned && (
            <RemoveModel
              providerId={providerId}
              providerName={providerName}
              model={detail.model}
              onRemoved={() => {
                onRemoved(detail.model.model_id);
                onClose();
              }}
            />
          )}
        </section>

        <ProviderModelEntryInfo
          detail={detail}
          value={draft.entry}
          onChange={(entry) => setDraft({ entry })}
          onValidityChange={setValidity}
          onRestored={handleRestored}
        />
      </SheetBody>

      <SheetFooter className="flex-col items-stretch">
        {stopsFollowing && detail.entry && (
          <p className="text-xs text-warn">
            {t("pm_entry_save_stops_following")}
            {detail.entry.shared_with.length > 0 &&
              t("pm_entry_save_shared", { providers: providerNames(t, detail.entry) })}
          </p>
        )}
        <SaveBar unit={unit} />
      </SheetFooter>
    </>
  );
}

function Subsection({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  const headingId = useId();
  return (
    <div role="group" aria-labelledby={headingId} className="flex flex-col gap-2">
      <div className="flex flex-col gap-0.5">
        <h4 id={headingId} className="text-sm font-medium">
          {title}
        </h4>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      {children}
    </div>
  );
}

/** 「从『X』移除」：先确认；有设置或项目在用这个模型时在确认框里列出。 */
function RemoveModel({
  providerId,
  providerName,
  model,
  onRemoved,
}: {
  providerId: string;
  providerName: string;
  model: ProviderModelItem;
  onRemoved: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [open, setOpen] = useState(false);
  const [references, setReferences] = useState<ProviderModelReferences | null>(null);
  const [referencesError, setReferencesError] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const name = model.display_name ?? model.model_id;
  const title = t("pm_sheet_remove_title", { provider: providerName, model: name });

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    voidCall(
      API.getProviderModelReferences(providerId, model.model_id, { signal: controller.signal })
        .then((res) => {
          if (!controller.signal.aborted) setReferences(res);
        })
        .catch((err: unknown) => {
          if (!controller.signal.aborted) setReferencesError(errMsg(err));
        }),
    );
    return () => controller.abort();
  }, [open, providerId, model.model_id]);

  // 被移除的模型连同 Sheet 里未保存的修改一起消失，确认框本身就是这次确认，不再经离开拦截
  const handleRemove = async () => {
    setRemoving(true);
    setRemoveError(null);
    try {
      await API.removeProviderModel(providerId, model.model_id);
      useCapabilitiesStore.getState().invalidate();
      setOpen(false);
      onRemoved();
    } catch (err) {
      setRemoveError(errMsg(err));
    } finally {
      setRemoving(false);
    }
  };

  const used = references !== null && (references.settings.length > 0 || references.projects.length > 0);

  return (
    <>
      <Button
        variant="destructive"
        size="sm"
        className="self-start"
        onClick={() => {
          setReferences(null);
          setReferencesError(null);
          setRemoveError(null);
          setOpen(true);
        }}
      >
        <Trash2 aria-hidden data-icon="inline-start" />
        {t("pm_sheet_remove", { name: providerName })}
      </Button>
      <AlertDialog
        open={open}
        onOpenChange={(next) => {
          if (!next && !removing) setOpen(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{title}</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogBody tabIndex={0} role="region" aria-label={title}>
            <div className="flex flex-col gap-2">
              <AlertDialogDescription>{t("pm_sheet_remove_description")}</AlertDialogDescription>
              {references === null && !referencesError && (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 aria-hidden className="size-4 animate-spin" />
                  {t("pm_sheet_remove_references_loading")}
                </p>
              )}
              {referencesError && (
                <p className="text-sm text-warn">{t("pm_sheet_remove_references_failed", { message: referencesError })}</p>
              )}
              {used && (
                <div className="flex flex-col gap-1">
                  <p className="text-sm text-warn">{t("pm_sheet_remove_references")}</p>
                  <ul className="flex list-disc flex-col gap-0.5 pl-5 text-sm">
                    {references.settings.map((key) => (
                      <li key={`setting:${key}`}>
                        {t("pm_sheet_remove_setting_item", { name: t(`global_bucket_label_${key}`) })}
                      </li>
                    ))}
                    {references.projects.map((project) => (
                      <li key={`project:${project.name}`}>
                        {t("pm_sheet_remove_project_item", { name: project.title ?? project.name })}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {removeError && (
                <p role="alert" className="text-sm wrap-break-word text-destructive">
                  {t("pm_sheet_remove_failed", { message: removeError })}
                </p>
              )}
            </div>
          </AlertDialogBody>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removing}>{t("common:cancel")}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" disabled={removing} onClick={() => void handleRemove()}>
              {removing && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
              {t("pm_sheet_remove_confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
