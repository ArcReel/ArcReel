import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { FileJson2, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { errMsg, voidCall } from "@/utils/async";
import { useAppStore } from "@/stores/app-store";
import {
  ACCENT_BTN_SM_CLS,
  ACCENT_BUTTON_STYLE,
  CARD_STYLE,
  GHOST_BTN_CLS,
} from "@/components/ui/darkroom-tokens";
import type {
  ComfyuiBindingKey,
  ComfyuiBindingTarget,
  ComfyuiBindings,
  ComfyuiEndpointDefinition,
  ComfyuiInferResponse,
  ComfyuiMediaType,
  CustomEndpointInfo,
} from "@/types";
import {
  bindingsFromInference,
  classTypeCounts,
  definitionFingerprint,
  pruneBindings,
  saveBlockers,
  workflowNodes,
  type ComfyuiSaveBlocker,
} from "./comfyui-bindings";
import { ComfyuiBindingTable } from "./ComfyuiBindingTable";
import { exportEndpointDefinition } from "./export-endpoint-definition";

/** 自动包装原始 workflow 时写进 `meta.name` 的占位值，与服务端 `import_shapes.py` 同一个。 */
export const COMFYUI_PLACEHOLDER_NAME = "ComfyUI workflow";

const KICKER_CLS = "font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2";
const MEDIA_TYPES: readonly ComfyuiMediaType[] = ["video", "image"];
/** `auth` 节里的两张表，按渲染次序。 */
const AUTH_SECTIONS = ["headers", "query"] as const;

function Section({
  kicker,
  title,
  description,
  children,
}: {
  kicker: string;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="mb-6">
      <div className={KICKER_CLS}>{kicker}</div>
      <h3 className="mt-0.5 text-[14px] font-medium text-text">{title}</h3>
      {description && <p className="mt-0.5 max-w-2xl text-[12px] leading-[1.55] text-text-3">{description}</p>}
      <div className="mt-2.5 rounded-[10px] border border-hairline p-4" style={CARD_STYLE}>
        {children}
      </div>
    </section>
  );
}

export interface ComfyuiEndpointDetailProps {
  /** 已保存的端点；从导入进来、还没保存的草稿为 null。 */
  record: CustomEndpointInfo | null;
  definition: ComfyuiEndpointDefinition;
  /** 导入时那份文件的名字；已保存的端点没有来源文件。 */
  sourceFileName: string | null;
  /** 导入时已经跑过一轮推断的话带过来，省掉进详情后的第二次请求。 */
  initialInference: ComfyuiInferResponse | null;
  referenceCount: number;
  onSaved: (record: CustomEndpointInfo) => void;
  /** 把当前这份草稿交出去，重新导入的新 workflow 接到它上面。 */
  onReimport: (current: ComfyuiEndpointDefinition) => void;
  deleteButton: ReactNode;
}

/**
 * ComfyUI 端点详情：头部 → 节点绑定表 → 定义 → workflow 本体。
 *
 * 推断只产出候选，用户在绑定表里确认后随定义一并落盘（`docs/adr/0082`）；服务端不留状态，
 * 因此每次进来都拿当前这份定义（连同它已确认的节点绑定）重跑一次，重导入的重匹配也走同一条路。
 */
export function ComfyuiEndpointDetail({
  record,
  definition: initialDefinition,
  sourceFileName,
  initialInference,
  referenceCount,
  onSaved,
  onReimport,
  deleteButton,
}: ComfyuiEndpointDetailProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const pushToast = useAppStore((s) => s.pushToast);

  const [definition, setDefinition] = useState(initialDefinition);
  const [bindings, setBindings] = useState<ComfyuiBindings>(() =>
    initialInference ? bindingsFromInference(initialInference, initialDefinition.media_type) : {},
  );
  const [touched, setTouched] = useState<ReadonlySet<ComfyuiBindingKey>>(() => new Set());
  const [inference, setInference] = useState<ComfyuiInferResponse | null>(initialInference);
  const [inferError, setInferError] = useState<string | null>(null);
  const [reinferring, setReinferring] = useState<ComfyuiBindingKey | null>(null);
  const [saving, setSaving] = useState(false);
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
  const canSave = inference !== null && blockers.length === 0 && dirty && !saving && reinferring === null;

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

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      const saved =
        record === null
          ? await API.createCustomEndpoint(draft)
          : await API.updateCustomEndpoint(record.id, draft);
      setSavedJson(definitionFingerprint(saved.definition));
      pushToast(t("ce_saved"), "success");
      onSaved(saved);
    } catch (e) {
      pushToast(errMsg(e, t("ce_save_failed")), "error");
    } finally {
      setSaving(false);
    }
  }, [draft, record, onSaved, pushToast, t]);

  return (
    <div className="px-6 py-6">
      <div className="mb-6 flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className={KICKER_CLS}>Workflow endpoint</div>
          <div className="mt-1 flex flex-wrap items-center gap-2.5">
            <input
              value={definition.meta.name}
              aria-label={t("ce_cf_name_label")}
              placeholder={t("ce_cf_name_placeholder")}
              autoComplete="off"
              onChange={(event) =>
                setDefinition((current) => ({
                  ...current,
                  meta: { ...current.meta, name: event.target.value },
                }))
              }
              className="min-w-0 flex-1 border-b border-transparent bg-transparent font-editorial text-[20px] text-text outline-none placeholder:text-text-4 hover:border-hairline focus:border-accent/50"
            />
            <span className="shrink-0 rounded-[5px] border border-accent/35 bg-accent-dim px-1.5 py-0.5 font-mono text-[9.5px] font-bold uppercase tracking-[0.1em] text-accent-2">
              comfyui
            </span>
            <span className="shrink-0 rounded-[5px] border border-hairline-soft bg-bg-grad-a/55 px-1.5 py-0.5 font-mono text-[9.5px] font-bold uppercase tracking-[0.1em] text-text-3">
              {t(definition.media_type === "image" ? "endpoint_image_group" : "endpoint_video_group")}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[12px] text-text-3">
            {record && (
              <span className="font-mono text-[11.5px]" translate="no">
                {record.key}
              </span>
            )}
            {sourceFileName && <span>{t("ce_cf_source_file", { file: sourceFileName })}</span>}
            <span>{t("ce_cf_node_count", { n: nodes.length })}</span>
            {referenceCount > 0 && <span>{t("ce_reference_count", { n: referenceCount })}</span>}
          </div>
        </div>

        <button type="button" onClick={() => onReimport(draft)} className={GHOST_BTN_CLS}>
          <FileJson2 className="h-3.5 w-3.5" aria-hidden />
          {t("ce_cf_reimport")}
        </button>
        <button
          type="button"
          onClick={() => exportEndpointDefinition(draft, record?.installation?.slug)}
          className={GHOST_BTN_CLS}
        >
          {t("ce_cf_export_definition")}
        </button>
        {deleteButton}
      </div>

      <Section kicker="Bindings" title={t("ce_cf_bindings_title")}>
        {inferError !== null && (
          <p role="alert" className="mb-2.5 text-[12.5px] text-warm-bright">
            {t("ce_cf_infer_failed", { reason: inferError })}
          </p>
        )}
        {inference === null ? (
          inferError === null && (
            <div className="flex items-center gap-2 py-4 text-text-3">
              <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
              <span className="font-mono text-[11px] uppercase tracking-[0.14em]">{t("ce_cf_inferring")}</span>
            </div>
          )
        ) : (
          <>
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
            <div className="mt-3 flex flex-wrap items-start gap-3 border-t border-hairline-soft pt-3">
              <button
                type="button"
                disabled={!canSave}
                onClick={() => void handleSave()}
                className={ACCENT_BTN_SM_CLS}
                style={ACCENT_BUTTON_STYLE}
              >
                {saving && <Loader2 className="h-3 w-3 motion-safe:animate-spin" aria-hidden />}
                {dirty ? t("ce_cf_save") : t("ce_cf_saved_state")}
              </button>
              {blockers.length > 0 ? (
                <ul aria-live="polite" className="space-y-0.5 text-[11.5px] text-warm-bright">
                  {blockers.map((blocker) => (
                    <li key={`${blocker.code}:${blocker.key ?? ""}:${blocker.otherKey ?? ""}`}>
                      {blockerText(blocker)}
                    </li>
                  ))}
                </ul>
              ) : (
                dirty && <span className="text-[11.5px] text-text-4">{t("ce_cf_save_ready")}</span>
              )}
            </div>
          </>
        )}
      </Section>

      <Section kicker="Definition" title={t("ce_cf_definition_title")}>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <span className="mb-1 block text-[11.5px] font-medium text-text-3">{t("ce_cf_media_type_label")}</span>
            <div className="flex gap-2">
              {MEDIA_TYPES.map((media) => (
                <button
                  key={media}
                  type="button"
                  aria-pressed={definition.media_type === media}
                  onClick={() => changeMediaType(media)}
                  className={`rounded-[7px] border px-3 py-1 font-mono text-[11.5px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                    definition.media_type === media
                      ? "border-accent/45 bg-accent-dim text-accent-2"
                      : "border-hairline-soft text-text-3 hover:text-text"
                  }`}
                >
                  {t(media === "image" ? "endpoint_image_group" : "endpoint_video_group")}
                </button>
              ))}
            </div>
            <p className="mt-1.5 text-[11px] leading-[1.5] text-text-4">{t("ce_cf_media_type_note")}</p>
          </div>
          <div>
            <span className="mb-1 block text-[11.5px] font-medium text-text-3">{t("ce_cf_auth_label")}</span>
            <pre className="overflow-x-auto rounded-[7px] border border-hairline-soft bg-bg-grad-a/40 p-2 font-mono text-[11.5px] text-text-2">
              {authPreview(definition)}
            </pre>
            <p className="mt-1.5 text-[11px] leading-[1.5] text-text-4">
              {t(definition.auth ? "ce_cf_auth_blank_note" : "ce_cf_auth_empty")}
            </p>
          </div>
        </div>
      </Section>

      <Section kicker="Workflow" title={t("ce_cf_workflow_title")} description={t("ce_cf_workflow_desc")}>
        <div className="flex flex-wrap gap-1.5">
          {chips.map((chip) => (
            <span
              key={chip.classType}
              translate="no"
              className="rounded-[5px] border border-hairline-soft px-1.5 py-0.5 font-mono text-[11.5px] text-text-3"
            >
              {chip.classType} <span className="tabular-nums text-text-4">×{chip.count}</span>
            </span>
          ))}
        </div>
      </Section>
    </div>
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

