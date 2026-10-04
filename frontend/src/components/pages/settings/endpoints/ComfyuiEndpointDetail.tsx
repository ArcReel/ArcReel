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
import { EndpointUsageList, type EndpointUsage } from "./EndpointUsageList";
import { exportEndpointDefinition } from "./export-endpoint-definition";

/** 自动包装原始 workflow 时写进 `meta.name` 的占位值，与服务端 `import_shapes.py` 同一个。 */
export const COMFYUI_PLACEHOLDER_NAME = "ComfyUI workflow";

const MEDIA_TYPES: readonly ComfyuiMediaType[] = ["video", "image"];
/** `auth` 节里的两张表，按渲染次序。 */
const AUTH_SECTIONS = ["headers", "query"] as const;
/** 「已保存」在保存栏上停留的时长，与 `useEditUnit` 一致。 */
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
  /** 已保存的端点；从导入进来、还没保存的端点为 null。 */
  record: CustomEndpointInfo | null;
  definition: ComfyuiEndpointDefinition;
  /** 导入时那份文件的名字；已保存的端点没有来源文件。 */
  sourceFileName: string | null;
  /** 导入时已经跑过一轮推断的话带过来，省掉进详情后的第二次请求。 */
  initialInference: ComfyuiInferResponse | null;
  usages: EndpointUsage[];
  back: EndpointBackTarget | null;
  /** 测试连接的凭证来源；只列 comfyui 协议的供应商，别的协议连不上这台机器。 */
  providers: CustomProviderInfo[];
  onSaved: (record: CustomEndpointInfo) => void;
  /** 把当前这份定义交出去，重新导入的新 workflow 接到它上面。 */
  onReimport: (current: ComfyuiEndpointDefinition) => void;
  /** 放弃未保存修改：上层丢掉导入的未保存定义，并重建本组件回到已保存内容。 */
  onDiscard: () => void;
  /** 「更多操作」里由上层提供的条目（分享、删除）。 */
  menuItems: (hasUnsavedChanges: boolean, pending: boolean) => ReactNode;
  submissionBadge?: ReactNode;
}

/**
 * ComfyUI 端点详情：页头 → 节点绑定表 → 定义 → workflow 本体 → 端点测试，保存栏在详情栏底部。
 *
 * 推断只产出候选，用户在绑定表里确认后随定义一并落盘（`docs/adr/0082`）；服务端不留状态，
 * 因此每次进来都拿当前这份定义（连同它已确认的节点绑定）重跑一次，重导入的重匹配也走同一条路。
 * 未保存修改由本组件自管（推断结果与手动接管交织在一起），经 `useLeaveGuard` 登记离开拦截。
 */
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
  menuItems,
  submissionBadge,
}: ComfyuiEndpointDetailProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const nameId = useId();
  const mediaLabelId = useId();

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

  // 推断链轮换：换媒体类型与逐键重新识别都作废上一轮，避免旧结果盖掉新的。
  const inferRef = useRef<AbortController | null>(null);
  // 各语义键被手动定过的次数。逐键重新识别发出时记下这一格的次数，结果回来时次数已经变了，
  // 说明用户在等待期间亲手定过这个键：他后按的那一下比在途的推断新，推断结果不再压过去。
  const manualSeq = useRef(new Map<ComfyuiBindingKey, number>());

  /**
   * 跑一轮推断并接手结果。`focusKey` 非空时只换这一个语义键（逐键重新识别），其余原样保留，
   * 且这一格的手动接管序号仍是 `sinceEdit` 才写——等待期间被用户亲手定过就只留下推断结果本身，
   * 供候选列表使用。`focusKey` 为空时整表重来，只有 `keep` 里的条目压过新结果。
   *
   * 进详情那一轮由 effect 发起，所以第一个 await 之前不碰 state：重来一轮时该清的上一次错误
   * 由发起方在事件回调里清。
   */
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

  // 进详情时没有现成的推断结果就问一次；载荷是当前这份定义，它已确认的节点绑定即重匹配的输入。
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
      // 换媒体类型即换一套推断规则与语义键名录：越界的键连条目一起摘掉，没被用户定过的键
      // 一律按新规则重来，只留下用户亲手定过的那几条。
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
      // 推断未就绪或节点绑定还有问题时服务端不收这份定义；原因在绑定表上方列出。
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
            </>
          }
          primary={
            <Button variant="outline" onClick={() => onReimport(draft)}>
              <FileJson2 aria-hidden data-icon="inline-start" />
              {t("ce_cf_reimport")}
            </Button>
          }
          menu={
            <>
              <DropdownMenuItem onClick={() => exportEndpointDefinition(draft, record?.installation?.slug)}>
                {t("ce_export_json")}
              </DropdownMenuItem>
              {menuItems(dirty, saving || inference === null || reinferring !== null)}
            </>
          }
        />
      }
      footer={<SaveBar unit={controls} className="max-w-178" />}
    >
      <div className="flex max-w-190 flex-col gap-8 px-6 py-6">
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
                // 单选：再次点击已选项不取消选择
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
            <pre className="overflow-x-auto rounded-md border border-border bg-muted/40 p-3 font-mono text-xs text-subtle-foreground">
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
      </div>
    </DetailPane>
  );
}

/** 用户本轮亲手定过的那几条节点绑定。 */
function onlyTouched(bindings: ComfyuiBindings, touched: ReadonlySet<ComfyuiBindingKey>): ComfyuiBindings {
  const kept: ComfyuiBindings = {};
  for (const key of touched) {
    if (bindings[key] !== undefined) kept[key] = bindings[key];
  }
  return kept;
}

/**
 * 定义里配了什么就照它自己那份显示，一条也没配才给出这一节该长什么样的模板。
 *
 * 两张表都要看：只在 `query` 里配了凭据的端点实发时照样把它拼进 URL，这里却只认 `headers`
 * 的话，展示的是一句它根本不用的 `Authorization`。
 */
function authPreview(definition: ComfyuiEndpointDefinition): string {
  const configured = AUTH_SECTIONS.flatMap((name) => {
    const table = definition.auth?.[name];
    if (!table || Object.keys(table).length === 0) return [];
    return [[`${name}:`, ...Object.entries(table).map(([key, value]) => `  ${key}: ${value}`)].join("\n")];
  });
  return configured.length > 0 ? configured.join("\n") : "headers:\n  Authorization: Bearer {{ api_key }}";
}

