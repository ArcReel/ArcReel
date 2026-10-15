import { useEffect, useId, useState } from "react";
import { Link2, Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { CatalogEntryPicker } from "@/components/shared/CatalogEntryPicker";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type {
  CatalogEntryHit,
  DiscoveryFormat,
  FetchedProviderModel,
  FetchProviderModelsResponse,
  MediaType,
} from "@/types";
import { errMsg, voidCall } from "@/utils/async";
import { EndpointSelect } from "./EndpointSelect";
import { MEDIA_LABEL_KEY } from "./providerModelDisplay";

/** 勾选要添加的一个模型：自定义供应商带调用端点，未识别的模型可以带用户选的对应模型。 */
export interface FetchSelection {
  model: FetchedProviderModel;
  /** 添加后的媒体类型：选了对应模型时取对应模型的，否则取获取结果的。 */
  mediaType: MediaType;
  endpoint: string | null;
  binding: CatalogEntryHit | null;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  providerName: string;
  /** 调用供应商的模型列表接口。 */
  load: (signal: AbortSignal) => Promise<FetchProviderModelsResponse>;
  /** 已添加的模型不列出，只计数。 */
  isAdded: (model: FetchedProviderModel) => boolean;
  /** 自定义供应商的模型发现协议：给出时每行有调用端点下拉。 */
  protocol?: DiscoveryFormat;
  /** 添加勾选的模型，按顺序返回每个模型的失败原因（成功为 null）。全部成功时对话框关闭。 */
  onAdd: (selections: FetchSelection[]) => Promise<(string | null)[]>;
}

/**
 * 「获取模型」对话框：调用供应商的模型列表接口，只列尚未添加的模型，分「已识别」（默认勾选）与「未识别」
 * 两组。未识别的行默认不勾，可以选一个「对应模型」，添加时存为条目绑定；名字已对应到条目的行显示条目名，
 * 只有对应不上条目、或条目没有价格的行提示费用会记为未定价。
 */
export function FetchModelsDialog({ open, onOpenChange, ...rest }: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size={rest.protocol ? "xl" : "lg"}>
        {open && <FetchModelsBody {...rest} onClose={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "not_applicable"; reason: string | null }
  | { status: "ready"; models: FetchedProviderModel[] };

interface RowState {
  checked: boolean;
  endpoint: string | null;
  binding: CatalogEntryHit | null;
  error: string | null;
}

function rowKey(model: FetchedProviderModel): string {
  return `${model.model_id}\u0000${model.model_key ?? ""}`;
}

function initialRows(models: FetchedProviderModel[]): Record<string, RowState> {
  return Object.fromEntries(
    models.map((m) => [rowKey(m), { checked: m.recognized, endpoint: m.endpoint, binding: null, error: null }]),
  );
}

function FetchModelsBody({
  providerName,
  load,
  isAdded,
  protocol,
  onAdd,
  onClose,
}: Omit<Props, "open" | "onOpenChange"> & { onClose: () => void }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const endpointToMediaType = useEndpointCatalogStore((s) => s.endpointToMediaType);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [rows, setRows] = useState<Record<string, RowState>>({});
  const [adding, setAdding] = useState(false);
  const [picking, setPicking] = useState<FetchedProviderModel | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    voidCall(
      load(controller.signal)
        .then((res) => {
          if (controller.signal.aborted) return;
          if (res.not_applicable) {
            setState({ status: "not_applicable", reason: res.reason });
            return;
          }
          setRows(initialRows(res.models));
          setState({ status: "ready", models: res.models });
        })
        .catch((err: unknown) => {
          if (!controller.signal.aborted) setState({ status: "error", message: errMsg(err) });
        }),
    );
    return () => controller.abort();
  }, [attempt, load]);

  const updateRow = (model: FetchedProviderModel, patch: Partial<RowState>) =>
    setRows((prev) => ({ ...prev, [rowKey(model)]: { ...prev[rowKey(model)], ...patch } }));

  const mediaOf = (model: FetchedProviderModel, row: RowState): MediaType => row.binding?.media_type ?? model.media_type;

  // 对应模型的媒体类型与调用端点不一致时，换成该媒体类型下的默认端点
  const bind = (model: FetchedProviderModel, binding: CatalogEntryHit | null) => {
    const row = rows[rowKey(model)];
    const media = binding?.media_type ?? model.media_type;
    let endpoint = row.endpoint;
    if (protocol && endpoint && endpointToMediaType[endpoint] !== media) {
      endpoint = model.endpoint_by_media_type?.[media] ?? endpoint;
    }
    updateRow(model, { binding, endpoint, checked: binding ? true : row.checked });
  };

  // 已添加的模型按当前列表现算：添加成功的行在上层刷新列表后自然移走
  const fetched = state.status === "ready" ? state.models : [];
  const models = fetched.filter((m) => !isAdded(m));
  const alreadyAdded = fetched.length - models.length;
  const selected = models.filter((m) => rows[rowKey(m)]?.checked);

  const add = async () => {
    setAdding(true);
    try {
      const selections = selected.map((model) => {
        const row = rows[rowKey(model)];
        return { model, mediaType: mediaOf(model, row), endpoint: row.endpoint, binding: row.binding };
      });
      const errors = await onAdd(selections);
      if (errors.every((e) => e === null)) {
        onClose();
        return;
      }
      // 添加成功的行取消勾选（上层刷新列表后移走），失败的行留下并写明原因
      setRows((prev) => {
        const next = { ...prev };
        selections.forEach((s, i) => {
          const error = errors[i] ?? null;
          next[rowKey(s.model)] = { ...next[rowKey(s.model)], checked: error !== null, error };
        });
        return next;
      });
    } catch (err) {
      setRows((prev) => {
        const next = { ...prev };
        for (const model of selected) next[rowKey(model)] = { ...next[rowKey(model)], error: errMsg(err) };
        return next;
      });
    } finally {
      setAdding(false);
    }
  };

  const recognized = models.filter((m) => m.recognized);
  const unrecognized = models.filter((m) => !m.recognized);

  const renderRow = (model: FetchedProviderModel) => {
    const row = rows[rowKey(model)];
    if (!row) return null;
    const media = mediaOf(model, row);
    const entryName = row.binding?.display_name ?? model.matched_entry?.display_name;
    // 用户选了对应模型的行不提示：搜索结果不带价格，选择即视为用户的决定
    const unpriced = !model.recognized && !row.binding && !model.matched_entry?.priced;
    return (
      <FetchedRow
        key={rowKey(model)}
        model={model}
        row={row}
        media={media}
        entryName={entryName}
        unpriced={unpriced}
        protocol={protocol}
        disabled={adding}
        onCheck={(checked) => updateRow(model, { checked })}
        onEndpoint={(endpoint) => updateRow(model, { endpoint })}
        onPickEntry={() => setPicking(model)}
        onClearEntry={() => bind(model, null)}
      />
    );
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t("pm_fetch_title", { name: providerName })}</DialogTitle>
        <DialogDescription>
          {models.length > 0 ? t("pm_fetch_description") : t("pm_fetch_description_idle")}
        </DialogDescription>
      </DialogHeader>
      <DialogBody aria-busy={state.status === "loading"}>
        <div className="flex flex-col gap-4">
          {state.status === "loading" && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
              {t("pm_fetch_loading")}
            </p>
          )}
          {state.status === "error" && (
            <div role="alert" className="flex flex-col items-start gap-2 text-sm">
              <p>{t("pm_fetch_failed")}</p>
              <details className="text-xs text-muted-foreground">
                <summary>{t("catalog_picker_error_details")}</summary>
                <span className="wrap-break-word">{state.message}</span>
              </details>
              <Button variant="outline" size="sm" onClick={() => setAttempt((n) => n + 1)}>
                {t("common:retry")}
              </Button>
            </div>
          )}
          {state.status === "not_applicable" && (
            <p className="text-sm text-muted-foreground">{state.reason ?? t("discovery_not_applicable")}</p>
          )}
          {state.status === "ready" && models.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {alreadyAdded > 0 ? t("pm_fetch_all_added") : t("pm_fetch_none")}
            </p>
          )}
          {recognized.length > 0 && (
            <FetchGroup title={t("pm_fetch_recognized")} count={recognized.length}>
              {recognized.map(renderRow)}
            </FetchGroup>
          )}
          {unrecognized.length > 0 && (
            <FetchGroup title={t("pm_fetch_unrecognized")} count={unrecognized.length} hint={t("pm_fetch_unrecognized_hint")}>
              {unrecognized.map(renderRow)}
            </FetchGroup>
          )}
          {models.length > 0 && alreadyAdded > 0 && (
            <p className="text-xs text-muted-foreground">{t("pm_fetch_already_added", { count: alreadyAdded })}</p>
          )}
        </div>
      </DialogBody>
      <DialogFooter>
        <Button variant="outline" disabled={adding} onClick={onClose}>
          {models.length === 0 ? t("common:close") : t("common:cancel")}
        </Button>
        {models.length > 0 && (
          <Button disabled={selected.length === 0 || adding} onClick={() => voidCall(add())}>
            {adding && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {t("pm_fetch_add", { count: selected.length })}
          </Button>
        )}
      </DialogFooter>
      {picking && (
        <CatalogEntryPicker
          open
          onOpenChange={(next) => {
            if (!next) setPicking(null);
          }}
          mediaType={picking.media_type}
          title={t("pm_fetch_match_title", { name: picking.model_id })}
          onSelect={(entry) => bind(picking, entry)}
        />
      )}
    </>
  );
}

function FetchGroup({
  title,
  count,
  hint,
  children,
}: {
  title: string;
  count: number;
  hint?: string;
  children: React.ReactNode;
}) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-1.5">
      <div className="flex flex-col gap-0.5">
        <h3 id={headingId} className="text-sm font-medium">
          {title}
          <span className="ml-1.5 text-muted-foreground tabular-nums">{count}</span>
        </h3>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      <ul className="flex flex-col divide-y divide-border/60 rounded-lg border border-border">{children}</ul>
    </section>
  );
}

function FetchedRow({
  model,
  row,
  media,
  entryName,
  unpriced,
  protocol,
  disabled,
  onCheck,
  onEndpoint,
  onPickEntry,
  onClearEntry,
}: {
  model: FetchedProviderModel;
  row: RowState;
  media: MediaType;
  entryName: string | undefined;
  unpriced: boolean;
  protocol: DiscoveryFormat | undefined;
  disabled: boolean;
  onCheck: (checked: boolean) => void;
  onEndpoint: (endpoint: string) => void;
  onPickEntry: () => void;
  onClearEntry: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const checkboxId = useId();
  return (
    <li className="flex flex-col gap-1 px-3 py-2">
      <div className="flex items-center gap-3">
        <Checkbox id={checkboxId} checked={row.checked} disabled={disabled} onCheckedChange={onCheck} />
        <label htmlFor={checkboxId} className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="font-mono text-sm break-all">{model.model_id}</span>
          <span className="truncate text-xs text-muted-foreground">
            {entryName ? `${entryName}（${t(MEDIA_LABEL_KEY[media])}）` : t(MEDIA_LABEL_KEY[media])}
          </span>
        </label>
        {!model.recognized &&
          (row.binding ? (
            <Button
              variant="ghost"
              size="icon-sm"
              disabled={disabled}
              aria-label={t("pm_fetch_match_clear", { name: model.model_id })}
              onClick={onClearEntry}
            >
              <X aria-hidden />
            </Button>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              disabled={disabled}
              aria-label={t("pm_fetch_match_for", { name: model.model_id })}
              onClick={onPickEntry}
            >
              <Link2 aria-hidden data-icon="inline-start" />
              {t("pm_fetch_match")}
            </Button>
          ))}
        {protocol && row.endpoint !== null && (
          <div className="w-56 shrink-0">
            <EndpointSelect
              compact
              aria-label={t("pm_fetch_endpoint_for", { name: model.model_id })}
              value={row.endpoint}
              onChange={onEndpoint}
              protocol={protocol}
              disabled={disabled}
            />
          </div>
        )}
      </div>
      {unpriced && <p className="pl-7 text-xs text-warn">{t("pm_fetch_row_unpriced")}</p>}
      {row.error && (
        <p role="alert" className="pl-7 text-xs wrap-break-word text-destructive">
          {t("pm_fetch_add_failed", { message: row.error })}
        </p>
      )}
    </li>
  );
}
