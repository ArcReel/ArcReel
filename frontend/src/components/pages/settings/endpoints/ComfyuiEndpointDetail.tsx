import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { FileJson2, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { errMsg, voidCall } from "@/utils/async";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { useLeaveGuard } from "@/components/shared/edit-unit/LeaveGuard";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import type { SaveStatus } from "@/components/shared/edit-unit/useEditUnit";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type {
  ComfyuiBindingKey,
  ComfyuiBindingTarget,
  ComfyuiBindings,
  ComfyuiEndpointDefinition,
  ComfyuiInferResponse,
  ComfyuiMediaType,
  CustomEndpointInfo,
  CustomProviderInfo,
  EndpointInstallation,
} from "@/types";
import {
  bindingsFromInference,
  classTypeCounts,
  definitionFingerprint,
  pruneBindings,
  saveBlockers,
  testRefused,
  workflowNodes,
  type ComfyuiSaveBlocker,
} from "./comfyui-bindings";
import { ComfyuiBindingTable } from "./ComfyuiBindingTable";
import { ComfyuiEndpointTestSection } from "./ComfyuiEndpointTestSection";
import { EndpointHeader, type EndpointBackTarget } from "./EndpointHeader";
import { EndpointInstallBadges, MarketOrigin, MarketUpdateButton } from "./EndpointMarketInfo";
import type { EndpointUsage } from "./endpoint-usages";
import { EndpointUsageList } from "./EndpointUsageList";
import { exportEndpointDefinition } from "./export-endpoint-definition";

export const COMFYUI_PLACEHOLDER_NAME = "ComfyUI workflow";

const MEDIA_TYPES: readonly ComfyuiMediaType[] = ["video", "image"];
const AUTH_SECTIONS = ["headers", "query"] as const;
const SAVED_DISPLAY_MS = 2000;

function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h3 id={headingId} className="text-base font-medium">
          {title}
        </h3>
        {description && <p className="max-w-[40em] text-sm text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  );
}

export interface ComfyuiEndpointDetailProps {
  record: CustomEndpointInfo | null;
  definition: ComfyuiEndpointDefinition;
  sourceFileName: string | null;
  initialInference: ComfyuiInferResponse | null;
  usages: EndpointUsage[];
  back: EndpointBackTarget | null;
  providers: CustomProviderInfo[];
  onSaved: (record: CustomEndpointInfo) => void;
  onReimport: (current: ComfyuiEndpointDefinition) => void;
  onDiscard: () => void;
  onUpdateFromMarket: (
    installation: EndpointInstallation,
    currentDefinition: ComfyuiEndpointDefinition,
    hasUnsavedChanges: boolean,
  ) => void;
  marketUpdatePending: boolean;
  menuItems: (hasUnsavedChanges: boolean, pending: boolean) => ReactNode;
  submissionBadge?: ReactNode;
}

export function ComfyuiEndpointDetail({
  record,
  definition: initialDefinition,
  sourceFileName,
  initialInference,
  usages,
  back,
  providers,
  onSaved,
  onReimport,
  onDiscard,
  onUpdateFromMarket,
  marketUpdatePending,
  menuItems,
  submissionBadge,
}: ComfyuiEndpointDetailProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const nameId = useId();
  const mediaLabelId = useId();
  const installation = record?.installation ?? null;

  const [definition, setDefinition] = useState(initialDefinition);
  const [bindings, setBindings] = useState<ComfyuiBindings>(() =>
    initialInference ? bindingsFromInference(initialInference, initialDefinition.media_type) : {},
  );
  const [touched, setTouched] = useState<ReadonlySet<ComfyuiBindingKey>>(() => new Set());
  const [inference, setInference] = useState<ComfyuiInferResponse | null>(initialInference);
  const [inferError, setInferError] = useState<string | null>(null);
  const [reinferring, setReinferring] = useState<ComfyuiBindingKey | null>(null);
  const [saveState, setSaveState] = useState<{ status: SaveStatus; error: string | null }>({
    status: "idle",
    error: null,
  });
  const saving = saveState.status === "saving";
  const [savedJson, setSavedJson] = useState<string | null>(() =>
    record ? definitionFingerprint(record.definition) : null,
  );

  const inferRef = useRef<AbortController | null>(null);
  const manualSeq = useRef(new Map<ComfyuiBindingKey, number>());

  const load = useCallback(
    async (
      payload: ComfyuiEndpointDefinition,
      {
        focusKey = null,
        keep = {},
        sinceEdit = 0,
      }: { focusKey?: ComfyuiBindingKey | null; keep?: ComfyuiBindings; sinceEdit?: number } = {},
    ) => {
      inferRef.current?.abort();
      const controller = new AbortController();
      inferRef.current = controller;
      try {
        const result = await API.inferComfyuiBindings(payload, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setInference(result);
        const inferred = bindingsFromInference(result, payload.media_type);
        if (focusKey === null) {
          setBindings({ ...inferred, ...keep });
        } else if ((manualSeq.current.get(focusKey) ?? 0) === sinceEdit) {
          setBindings((current) => {
            const next = { ...current };
            const fresh = inferred[focusKey];
            if (fresh === undefined) delete next[focusKey];
            else next[focusKey] = fresh;
            return next;
          });
          setTouched((current) => {
            const next = new Set(current);
            next.delete(focusKey);
            return next;
          });
        }
      } catch (e) {
        if (!controller.signal.aborted) setInferError(errMsg(e));
      } finally {
        if (inferRef.current === controller) {
          inferRef.current = null;
          setReinferring(null);
        }
      }
    },
    [],
  );

  const [mountDefinition] = useState(initialDefinition);
  useEffect(() => {
    if (initialInference !== null) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount-only 初始化，load 在首个 await 前不写 state
    voidCall(load(mountDefinition));
  }, [initialInference, mountDefinition, load]);

  useEffect(
    () => () => {
      inferRef.current?.abort();
      inferRef.current = null;
    },
    [],
  );

  const nodes = useMemo(() => workflowNodes(definition.workflow), [definition.workflow]);
  const chips = useMemo(() => classTypeCounts(nodes), [nodes]);

  const draft = useMemo<ComfyuiEndpointDefinition>(() => ({ ...definition, bindings }), [definition, bindings]);
  const draftJson = definitionFingerprint(draft);
  const dirty = draftJson !== savedJson;

  const blockers = inference ? saveBlockers(bindings, inference, definition, COMFYUI_PLACEHOLDER_NAME) : [];
  const pending = saving || inference === null || reinferring !== null;

  const changeBinding = useCallback((key: ComfyuiBindingKey, targets: ComfyuiBindingTarget[] | undefined) => {
    setBindings((current) => {
      const next = { ...current };
      if (targets === undefined) delete next[key];
      else next[key] = targets;
      return next;
    });
    manualSeq.current.set(key, (manualSeq.current.get(key) ?? 0) + 1);
    setTouched((current) => new Set(current).add(key));
  }, []);

  const reinfer = useCallback(
    (key: ComfyuiBindingKey) => {
      const without = { ...bindings };
      delete without[key];
      setReinferring(key);
      setInferError(null);
      voidCall(
        load({ ...definition, bindings: without }, { focusKey: key, sinceEdit: manualSeq.current.get(key) ?? 0 }),
      );
    },
    [bindings, definition, load],
  );

  const changeMediaType = useCallback(
    (next: ComfyuiMediaType) => {
      if (next === definition.media_type) return;
      const kept = pruneBindings(onlyTouched(bindings, touched), next);
      setDefinition((current) => ({ ...current, media_type: next }));
      setBindings(kept);
      setInference(null);
      setInferError(null);
      voidCall(load({ ...definition, media_type: next, bindings: kept }, { keep: kept }));
    },
    [bindings, definition, load, touched],
  );

  const blockerText = (blocker: ComfyuiSaveBlocker): string => {
    const name = blocker.key ? t(`ce_cf_key_${blocker.key}`) : "";
    switch (blocker.code) {
      case "aspect_mapping":
        return t("ce_cf_blocked_aspect_mapping");
      case "placeholder_name":
        return t("ce_cf_blocked_name");
      case "required_unbound":
        return t("ce_cf_blocked_required", { key: name });
      case "needs_choice":
        return t("ce_cf_blocked_choice", { key: name });
      case "needs_binding":
        return t("ce_cf_blocked_lost", { key: name });
      case "target_taken":
        return t("ce_cf_blocked_taken", {
          key: name,
          other: blocker.otherKey ? t(`ce_cf_key_${blocker.otherKey}`) : "",
        });
    }
  };

  const saveDefinition = useCallback(async (): Promise<boolean> => {
    if (!dirty) return true;
    setSaveState({ status: "saving", error: null });
    try {
      if (inference === null || reinferring !== null) throw new Error(t("ce_cf_inferring"));
      if (blockers.length > 0) throw new Error(t("ce_cf_save_blocked"));
      const saved =
        record === null ? await API.createCustomEndpoint(draft) : await API.updateCustomEndpoint(record.id, draft);
      setSavedJson(definitionFingerprint(saved.definition));
      setSaveState({ status: "saved", error: null });
      onSaved(saved);
      return true;
    } catch (e) {
      setSaveState({ status: "error", error: errMsg(e, t("ce_save_failed")) });
      return false;
    }
  }, [dirty, inference, reinferring, blockers.length, record, draft, onSaved, t]);

  useEffect(() => {
    if (saveState.status !== "saved") return;
    const timer = setTimeout(() => setSaveState((prev) => (prev.status === "saved" ? { ...prev, status: "idle" } : prev)), SAVED_DISPLAY_MS);
    return () => clearTimeout(timer);
  }, [saveState.status]);

  useLeaveGuard({ dirty, saving, save: saveDefinition, discard: onDiscard });

  const controls = {
    dirty,
    status: saveState.status,
    error: saveState.error,
    externallyUpdated: false,
    save: saveDefinition,
    discard: onDiscard,
  };

  return (
    <DetailPane
      header={
        <EndpointHeader
          back={back}
          title={definition.meta.name || t("ce_unnamed")}
          badges={
            <>
              <Badge variant="secondary">{t("ce_kind_comfyui")}</Badge>
              <Badge variant="outline">
                {t(definition.media_type === "image" ? "endpoint_image_group" : "endpoint_video_group")}
              </Badge>
              <EndpointInstallBadges installation={installation} />
              {submissionBadge}
            </>
          }
          meta={
            <>
              {record && (
                <span className="font-mono" translate="no">
                  {record.key}
                </span>
              )}
              {sourceFileName && <span className="min-w-0 break-all">{t("ce_cf_source_file", { file: sourceFileName })}</span>}
              <span>{t("ce_cf_node_count", { n: nodes.length })}</span>
              <MarketOrigin installation={installation} />
            </>
          }
          primary={
            <>
              <MarketUpdateButton
                installation={installation}
                pending={marketUpdatePending}
                disabled={pending}
                onUpdate={(target) => onUpdateFromMarket(target, draft, dirty)}
              />
              <Button variant="outline" onClick={() => onReimport(draft)} disabled={marketUpdatePending}>
                <FileJson2 aria-hidden data-icon="inline-start" />
                {t("ce_cf_reimport")}
              </Button>
            </>
          }
          menu={
            <>
              <DropdownMenuItem onClick={() => exportEndpointDefinition(draft, record?.installation?.slug)}>
                {t("ce_export_json")}
              </DropdownMenuItem>
              {menuItems(dirty, pending)}
            </>
          }
        />
      }
      footer={
        <fieldset disabled={marketUpdatePending}>
          <SaveBar unit={controls} className="max-w-178" />
        </fieldset>
      }
    >
      <fieldset disabled={marketUpdatePending} className="flex min-w-0 max-w-190 flex-col gap-8 px-6 py-6">
        {record && <EndpointUsageList usages={usages} />}

        <Section title={t("ce_cf_bindings_title")}>
          {inferError !== null && (
            <p role="alert" className="text-sm text-warn">
              {t("ce_cf_infer_failed", { reason: inferError })}
            </p>
          )}
          {inference === null ? (
            inferError === null && (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
                {t("ce_cf_inferring")}
              </p>
            )
          ) : (
            <>
              {blockers.length > 0 && (
                <Alert>
                  <AlertTitle>{t("ce_cf_blockers_title")}</AlertTitle>
                  <AlertDescription>
                    <ul aria-live="polite" className="flex list-disc flex-col gap-0.5 pl-4">
                      {blockers.map((blocker) => (
                        <li key={`${blocker.code}:${blocker.key ?? ""}:${blocker.otherKey ?? ""}`}>
                          {blockerText(blocker)}
                        </li>
                      ))}
                    </ul>
                  </AlertDescription>
                </Alert>
              )}
              <ComfyuiBindingTable
                nodes={nodes}
                mediaType={definition.media_type}
                inference={inference}
                bindings={bindings}
                touched={touched}
                onChange={changeBinding}
                onReinfer={reinfer}
                reinferring={reinferring}
              />
            </>
          )}
        </Section>

        <Section title={t("ce_cf_definition_title")}>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={nameId} className="text-sm font-medium">
              {t("ce_cf_name_label")}
            </label>
            <Input
              id={nameId}
              value={definition.meta.name}
              placeholder={t("ce_cf_name_placeholder")}
              autoComplete="off"
              onChange={(event) => {
                const name = event.target.value;
                setDefinition((current) => ({ ...current, meta: { ...current.meta, name } }));
              }}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <span id={mediaLabelId} className="text-sm font-medium">
              {t("ce_cf_media_type_label")}
            </span>
            <ToggleGroup
              aria-labelledby={mediaLabelId}
              variant="outline"
              size="sm"
              value={[definition.media_type]}
              onValueChange={(next: string[]) => {
                const media = MEDIA_TYPES.find((item) => item === next[0]);
                if (media) changeMediaType(media);
              }}
            >
              {MEDIA_TYPES.map((media) => (
                <ToggleGroupItem key={media} value={media}>
                  {t(media === "image" ? "endpoint_image_group" : "endpoint_video_group")}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
            <p className="text-xs text-muted-foreground">{t("ce_cf_media_type_note")}</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">{t("ce_cf_auth_label")}</span>
            <pre
              role="region"
              // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 只读的滚动区域需要键盘聚焦才能滚动
              tabIndex={0}
              aria-label={t("ce_cf_auth_label")}
              className="relative overflow-x-auto rounded-md border border-border bg-muted/40 p-3 font-mono text-xs text-subtle-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {authPreview(definition)}
            </pre>
            <p className="text-xs text-muted-foreground">
              {t(definition.auth ? "ce_cf_auth_blank_note" : "ce_cf_auth_empty")}
            </p>
          </div>
        </Section>

        <Section title={t("ce_cf_workflow_title")} description={t("ce_cf_workflow_desc")}>
          <ul className="flex flex-wrap gap-1.5">
            {chips.map((chip) => (
              <li key={chip.classType}>
                <Badge variant="outline" translate="no">
                  <span className="font-mono">{chip.classType}</span>
                  <span className="tabular-nums">×{chip.count}</span>
                </Badge>
              </li>
            ))}
          </ul>
        </Section>

        <Section title={t("ce_cf_test_title")} description={t("ce_cf_test_desc")}>
          <ComfyuiEndpointTestSection definition={draft} providers={providers} blocked={testRefused(blockers)} />
        </Section>
      </fieldset>
    </DetailPane>
  );
}

function onlyTouched(bindings: ComfyuiBindings, touched: ReadonlySet<ComfyuiBindingKey>): ComfyuiBindings {
  const kept: ComfyuiBindings = {};
  for (const key of touched) {
    if (bindings[key] !== undefined) kept[key] = bindings[key];
  }
  return kept;
}

function authPreview(definition: ComfyuiEndpointDefinition): string {
  const configured = AUTH_SECTIONS.flatMap((name) => {
    const table = definition.auth?.[name];
    if (!table || Object.keys(table).length === 0) return [];
    return [[`${name}:`, ...Object.entries(table).map(([key, value]) => `  ${key}: ${value}`)].join("\n")];
  });
  return configured.length > 0 ? configured.join("\n") : "headers:\n  Authorization: Bearer {{ api_key }}";
}

