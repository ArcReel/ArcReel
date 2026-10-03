import { Fragment, useId, useMemo, useState, type MouseEvent, type ReactNode } from "react";
import { Check, ChevronDown, ChevronRight, ExternalLink, Link2, Search, Store, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Link } from "wouter";
import { endpointSettingsPath, settingsSectionPath } from "@/app-routes";
import { ResolutionPicker } from "@/components/shared/ResolutionPicker";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Toggle } from "@/components/ui/toggle";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type { DiscoveryFormat, MediaType } from "@/types";
import { DurationParseError, parseDurationInput, type DurationParseErrorCode } from "@/utils/duration_format";
import { IMAGE_STANDARD_RESOLUTIONS, VIDEO_STANDARD_RESOLUTIONS, resolutionPlaceholder } from "@/utils/provider-models";
import { CapabilityOverrideRow } from "./CapabilityOverrideRow";
import { FieldHint, FieldRow } from "./CustomProviderFieldRow";
import {
  capabilityFieldsFor,
  globalBucketRefsFor,
  isComfyuiProtocol,
  priceLabel,
  withLastFrameOverride,
} from "./customProviderHelpers";
import { parsePositiveInt, type ModelRow } from "./customProviderFormState";
import { EndpointSelect } from "./EndpointSelect";

//: 模型多于这个数量时显示搜索框与按类型筛选。
const FILTER_THRESHOLD = 5;

const MEDIA_LABEL_KEY: Record<MediaType, string> = {
  text: "media_type_text",
  image: "media_type_image",
  video: "media_type_video",
  audio: "media_type_audio",
};
const MEDIA_ORDER = Object.keys(MEDIA_LABEL_KEY) as MediaType[];

type MediaFilter = "all" | MediaType;

const CURRENCY_SYMBOL: Record<string, string> = { USD: "$", CNY: "¥" };

export const DURATION_ERROR_KEY: Record<DurationParseErrorCode, string> = {
  empty_after_split: "supported_durations_err_empty_after_split",
  non_positive: "supported_durations_err_non_positive",
  exceeds_max: "supported_durations_err_exceeds_max",
  range_too_large: "supported_durations_err_range_too_large",
  range_inverted: "supported_durations_err_range_inverted",
  unparseable: "supported_durations_err_unparseable",
};

/** 支持秒数的格式错误；空值或格式正确时为 null。 */
export function durationsError(t: TFunction, text: string): string | null {
  if (!text.trim()) return null;
  try {
    parseDurationInput(text);
    return null;
  } catch (e) {
    if (e instanceof DurationParseError) return t(DURATION_ERROR_KEY[e.code], e.params);
    return t(DURATION_ERROR_KEY.unparseable, { seg: "" });
  }
}

// 档位为空的三支各说一句：三支互斥，两个文案位都为假即「帧率读得到、只是换算不出整秒时长」。
const EMPTY_TIER_COPY = {
  fixed: {
    placeholder: "supported_durations_fixed_placeholder",
    hint: "supported_durations_fixed_hint",
  },
  frameRateMissing: {
    placeholder: "supported_durations_no_fps_placeholder",
    hint: "supported_durations_no_fps_hint",
  },
  notDerivable: {
    placeholder: "supported_durations_not_derivable_placeholder",
    hint: "supported_durations_not_derivable_hint",
  },
} as const;

function priceSummary(row: ModelRow, media: MediaType | undefined, t: TFunction): string | null {
  if (!row.price_input && !row.price_output) return null;
  const symbol = CURRENCY_SYMBOL[row.currency] ?? `${row.currency} `;
  const unit = priceLabel(row.endpoint, media ? { [row.endpoint]: media } : {}, t);
  const input = row.price_input ? `${symbol}${row.price_input}${unit.input}` : null;
  const output = unit.output && row.price_output ? `${symbol}${row.price_output}${unit.output}` : null;
  return [input, output].filter(Boolean).join(" · ");
}

export interface ModelTableProps {
  /** 已按当前协议派生过端点的模型行。 */
  rows: ModelRow[];
  protocol: DiscoveryFormat;
  /** 已保存的供应商 id；新建表单没有。 */
  providerId?: number;
  expanded: ReadonlySet<string>;
  onToggleExpanded: (key: string) => void;
  onUpdate: (key: string, patch: Partial<ModelRow>) => void;
  onRemove: (key: string) => void;
  onToggleDefault: (key: string) => void;
  onSetEnabled: (keys: string[], enabled: boolean) => void;
}

/**
 * 自定义供应商的模型表格：列为启用、模型 ID、类型、调用端点、价格、默认。点击行在下方展开编辑区，
 * 可以同时展开多行；模型多于 5 个时显示搜索框与按类型筛选。
 */
export function ModelTable({
  rows,
  protocol,
  providerId,
  expanded,
  onToggleExpanded,
  onUpdate,
  onRemove,
  onToggleDefault,
  onSetEnabled,
}: ModelTableProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const endpoints = useEndpointCatalogStore((s) => s.endpoints);
  const endpointToMediaType = useEndpointCatalogStore((s) => s.endpointToMediaType);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<MediaFilter>("all");
  const searchId = useId();

  const showFilters = rows.length > FILTER_THRESHOLD;
  const presentMedia = useMemo(
    () => MEDIA_ORDER.filter((media) => rows.some((row) => endpointToMediaType[row.endpoint] === media)),
    [rows, endpointToMediaType],
  );
  // 筛选只在显示时生效：模型减到 5 个以内后筛选控件消失，残留的筛选条件不能继续藏行
  const activeFilter = showFilters && (filter === "all" || presentMedia.includes(filter)) ? filter : "all";
  const activeQuery = showFilters ? query.trim().toLowerCase() : "";
  const visible = rows.filter(
    (row) =>
      (!activeQuery || row.model_id.toLowerCase().includes(activeQuery)) &&
      (activeFilter === "all" || endpointToMediaType[row.endpoint] === activeFilter),
  );

  const enabledCount = visible.filter((row) => row.is_enabled).length;
  const allEnabled = visible.length > 0 && enabledCount === visible.length;

  const endpointLabel = (key: string) => {
    const endpoint = endpoints.find((e) => e.key === key);
    if (!endpoint) return key || t("cp_endpoint_unselected");
    return endpoint.display_name ?? t(endpoint.display_name_key);
  };

  return (
    <div className="flex flex-col gap-3">
      {showFilters && (
        <div className="flex flex-wrap items-center gap-2">
          <InputGroup className="w-56">
            <InputGroupAddon>
              <Search aria-hidden />
            </InputGroupAddon>
            <InputGroupInput
              id={searchId}
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("search_models")}
              aria-label={t("search_models")}
            />
          </InputGroup>
          <ToggleGroup
            aria-label={t("cp_model_type_filter")}
            variant="outline"
            size="sm"
            value={[activeFilter]}
            onValueChange={(next: string[]) => {
              if (next[0]) setFilter(next[0] as MediaFilter);
            }}
          >
            <ToggleGroupItem value="all">{t("cp_model_type_all")}</ToggleGroupItem>
            {presentMedia.map((media) => (
              <ToggleGroupItem key={media} value={media}>
                {t(MEDIA_LABEL_KEY[media])}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
      )}

      <div className="rounded-lg border border-border">
        <Table className="table-fixed">
          <colgroup>
            <col className="w-10" />
            <col />
            <col className="w-16" />
            <col />
            <col className="w-40" />
            <col className="w-20" />
            <col className="w-11" />
          </colgroup>
          <TableHeader>
            <TableRow>
              <TableHead>
                <Checkbox
                  checked={allEnabled}
                  indeterminate={enabledCount > 0 && !allEnabled}
                  disabled={visible.length === 0}
                  onCheckedChange={(checked) =>
                    onSetEnabled(
                      visible.map((row) => row.key),
                      checked,
                    )
                  }
                  aria-label={t("cp_enable_all_models")}
                />
              </TableHead>
              <TableHead>{t("model_id_label")}</TableHead>
              <TableHead>{t("cp_model_type_column")}</TableHead>
              <TableHead>{t("endpoint_label")}</TableHead>
              <TableHead>{t("cp_model_price_column")}</TableHead>
              <TableHead>{t("default_label")}</TableHead>
              <TableHead>
                <span className="sr-only">{t("cp_model_expand_column")}</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.length === 0 && (
              <TableRow>
                <TableCell colSpan={7}>
                  <p className="py-4 text-center text-sm text-muted-foreground">{t("cp_models_no_match")}</p>
                </TableCell>
              </TableRow>
            )}
            {visible.map((row) => {
              const open = expanded.has(row.key);
              const media = endpointToMediaType[row.endpoint];
              const price = priceSummary(row, media, t);
              const name = row.model_id || t("cp_model_id_missing");
              const editorId = `cp-model-editor-${row.key}`;
              // 点行任意位置展开或收起；行内的勾选框、按钮与输入框各有自己的动作，点它们不切换
              const onRowClick = (e: MouseEvent<HTMLTableRowElement>) => {
                if ((e.target as HTMLElement).closest("button, input, a, label, [role=checkbox]")) return;
                onToggleExpanded(row.key);
              };
              return (
                <Fragment key={row.key}>
                  <TableRow onClick={onRowClick}>
                    <TableCell>
                      <Checkbox
                        checked={row.is_enabled}
                        onCheckedChange={(checked) => onSetEnabled([row.key], checked)}
                        aria-label={t("cp_enable_model_named", { model: name })}
                      />
                    </TableCell>
                    <TableCell>
                      {row.model_id ? (
                        <TruncatedText
                          text={row.model_id}
                          className={row.is_enabled ? "font-mono" : "font-mono text-muted-foreground"}
                        />
                      ) : (
                        <span className="text-muted-foreground">{name}</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <span className="text-muted-foreground">{media ? t(MEDIA_LABEL_KEY[media]) : "—"}</span>
                    </TableCell>
                    <TableCell>
                      <TruncatedText text={endpointLabel(row.endpoint)} className="text-subtle-foreground" />
                    </TableCell>
                    <TableCell>
                      {price ? (
                        <TruncatedText text={price} className="text-subtle-foreground tabular-nums" />
                      ) : (
                        <span className="text-muted-foreground">{t("cp_model_price_unset")}</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Toggle
                        variant="outline"
                        size="sm"
                        pressed={row.is_default}
                        onPressedChange={() => onToggleDefault(row.key)}
                        aria-label={t("cp_model_default_named", { model: name })}
                      >
                        {row.is_default && <Check data-icon="inline-start" aria-hidden />}
                        {t("default_label")}
                      </Toggle>
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-expanded={open}
                        aria-controls={open ? editorId : undefined}
                        aria-label={t("cp_model_edit_named", { model: name })}
                        onClick={() => onToggleExpanded(row.key)}
                      >
                        {open ? <ChevronDown aria-hidden /> : <ChevronRight aria-hidden />}
                      </Button>
                    </TableCell>
                  </TableRow>
                  {open && (
                    <TableRow>
                      <TableCell colSpan={7}>
                        <div id={editorId} className="max-w-180 px-2 py-4 whitespace-normal">
                          <ModelFields
                            row={row}
                            protocol={protocol}
                            providerId={providerId}
                            onUpdate={(patch) => onUpdate(row.key, patch)}
                            onRemove={() => onRemove(row.key)}
                          />
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

interface ModelFieldsProps {
  row: ModelRow;
  protocol: DiscoveryFormat;
  providerId?: number;
  onUpdate: (patch: Partial<ModelRow>) => void;
  onRemove: () => void;
}

/** 展开的编辑区：属性共用一条标签列，「删除模型」在底部。 */
function ModelFields({ row, protocol, providerId, onUpdate, onRemove }: ModelFieldsProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const media = useEndpointCatalogStore((s) => s.endpointToMediaType[row.endpoint]);
  const constraints = useEndpointCatalogStore((s) => s.endpointConstraints[row.endpoint]);
  const endImageCapable = useEndpointCatalogStore((s) => s.endpointToEndImageCapable[row.endpoint] ?? false);
  const idPrefix = `cp-model-${row.key}`;
  const unit = priceLabel(row.endpoint, media ? { [row.endpoint]: media } : {}, t);
  const tierEmpty = constraints?.durationTierEmpty ?? false;
  const emptyCopy =
    EMPTY_TIER_COPY[
      constraints?.durationFixed ? "fixed" : constraints?.durationFrameRateMissing ? "frameRateMissing" : "notDerivable"
    ];
  const durationError = tierEmpty ? null : durationsError(t, row.supported_durations_text);
  const maxTokensInvalid = parsePositiveInt(row.max_output_tokens_text) === undefined;

  return (
    <div className="flex flex-col gap-3">
      <FieldRow label={t("model_id_label")} htmlFor={`${idPrefix}-id`}>
        <Input
          id={`${idPrefix}-id`}
          value={row.model_id}
          autoComplete="off"
          spellCheck={false}
          placeholder={t("cp_model_id_placeholder")}
          onChange={(e) => {
            const nextId = e.target.value;
            onUpdate({
              model_id: nextId,
              // 覆盖与判定都随 (endpoint, model_id) 作废/恢复，见 capabilityFieldsFor
              ...capabilityFieldsFor(row, nextId, row.endpoint),
              // 引用事实只绑 model_id，见 globalBucketRefsFor
              global_bucket_refs: globalBucketRefsFor(row, nextId),
            });
          }}
        />
      </FieldRow>

      <FieldRow label={t("endpoint_label")} htmlFor={`${idPrefix}-endpoint`}>
        <EndpointSelect
          id={`${idPrefix}-endpoint`}
          value={row.endpoint}
          protocol={protocol}
          onChange={(next) =>
            onUpdate({
              endpoint: next,
              is_default: false,
              // 覆盖的合法性本身随 endpoint 变化（last_frame 要求目标 endpoint 支持尾帧），
              // 切走即作废；切回原 endpoint 且 model_id 未变则原样取回。
              ...capabilityFieldsFor(row, row.model_id, next),
            })
          }
        />
        <div className="flex flex-wrap items-center gap-x-4">
          {row.endpoint && (
            <Link
              href={endpointSettingsPath(row.endpoint, { fromCustomProvider: providerId })}
              className={buttonVariants({ variant: "link", size: "sm" })}
            >
              <ExternalLink aria-hidden data-icon="inline-start" />
              {t("cp_open_endpoint")}
            </Link>
          )}
          <Link href={settingsSectionPath("market")} className={buttonVariants({ variant: "link", size: "sm" })}>
            <Store aria-hidden data-icon="inline-start" />
            {t("cp_endpoint_from_market")}
          </Link>
        </div>
      </FieldRow>

      <FieldRow label={t("cp_model_price_column")} group>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={row.currency || "USD"} onValueChange={(next) => next && onUpdate({ currency: next })}>
            <SelectTrigger aria-label={t("currency_label")} className="w-20">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="USD">$ USD</SelectItem>
              <SelectItem value="CNY">¥ CNY</SelectItem>
            </SelectContent>
          </Select>
          <PriceInput
            value={row.price_input}
            label={t("input_price")}
            unit={unit.input}
            onChange={(v) => onUpdate({ price_input: v })}
          />
          {unit.output && (
            <PriceInput
              value={row.price_output}
              label={t("output_price")}
              unit={unit.output}
              onChange={(v) => onUpdate({ price_output: v })}
            />
          )}
        </div>
      </FieldRow>

      {/* 最大输出长度（仅 text endpoint）：分集规划按它决定每批规划几集 */}
      {media === "text" && (
        <FieldRow
          label={t("max_output_tokens_label")}
          htmlFor={`${idPrefix}-max-tokens`}
          hint={
            maxTokensInvalid ? (
              <FieldHint tone="warn">{t("max_output_tokens_invalid")}</FieldHint>
            ) : (
              <FieldHint>{t("max_output_tokens_help")}</FieldHint>
            )
          }
        >
          <Input
            id={`${idPrefix}-max-tokens`}
            inputMode="numeric"
            className="w-40"
            value={row.max_output_tokens_text}
            onChange={(e) => onUpdate({ max_output_tokens_text: e.target.value })}
            placeholder={t("max_output_tokens_placeholder")}
            aria-invalid={maxTokensInvalid}
          />
        </FieldRow>
      )}

      {/* 分辨率（仅 image/video，audio 无分辨率维度） */}
      {(media === "image" || media === "video") && (
        <FieldRow
          label={t("resolution_label")}
          // 禁用原因必须有一行可见说明：title 对键盘与触屏不可达。
          hint={constraints?.sizeFixed ? <FieldHint>{t("resolution_fixed_hint")}</FieldHint> : undefined}
        >
          <ResolutionPicker
            mode="combobox"
            options={media === "image" ? IMAGE_STANDARD_RESOLUTIONS : VIDEO_STANDARD_RESOLUTIONS}
            value={row.resolution || null}
            onChange={(v) => onUpdate({ resolution: v ?? "" })}
            placeholder={resolutionPlaceholder(constraints, t)}
            aria-label={t("resolution_label")}
            disabled={constraints?.sizeFixed ?? false}
          />
        </FieldRow>
      )}

      {media === "video" && (
        <FieldRow
          label={t("supported_durations_label")}
          htmlFor={`${idPrefix}-durations`}
          hint={
            // 禁用原因必须有一行可见说明。缺帧率来源那一支是可修的定义，文案指向补哪里；
            // 换算不出整秒时长那一支补不出帧率来，不说成「补一处就能恢复」。
            tierEmpty ? (
              <FieldHint>{t(emptyCopy.hint)}</FieldHint>
            ) : durationError ? (
              <FieldHint tone="warn">{t("supported_durations_invalid", { message: durationError })}</FieldHint>
            ) : (
              <FieldHint>{t("supported_durations_help")}</FieldHint>
            )
          }
        >
          <Input
            id={`${idPrefix}-durations`}
            value={row.supported_durations_text}
            onChange={(e) => onUpdate({ supported_durations_text: e.target.value })}
            placeholder={t(tierEmpty ? emptyCopy.placeholder : "supported_durations_placeholder")}
            disabled={tierEmpty}
            aria-invalid={durationError !== null}
          />
        </FieldRow>
      )}

      {/* 能力覆盖（仅 video endpoint；首批只开放 last_frame）。ComfyUI 端点的能力只从节点绑定推导，
          服务端对该协议的覆盖写入一律 422，故不给入口。 */}
      {media === "video" && !isComfyuiProtocol(protocol) && (
        <FieldRow label={t("cap_override_last_frame_label")}>
          <CapabilityOverrideRow
            override={row.capability_overrides?.last_frame}
            systemValue={row.system_capabilities?.last_frame ?? null}
            endImageCapable={endImageCapable}
            onChange={(next) => onUpdate({ capability_overrides: withLastFrameOverride(row.capability_overrides, next) })}
          />
        </FieldRow>
      )}

      {/* 全局默认引用提示（只读，不影响保存） */}
      {row.global_bucket_refs.length > 0 && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Link2 aria-hidden className="size-3.5 shrink-0" />
          {t("global_bucket_ref_hint", {
            buckets: row.global_bucket_refs
              .map((key) => t(`global_bucket_label_${key}`))
              .join(t("global_bucket_ref_separator")),
          })}
        </p>
      )}

      <div className="flex justify-end">
        <Button variant="destructive" size="sm" onClick={onRemove}>
          <Trash2 aria-hidden data-icon="inline-start" />
          {t("delete_model")}
        </Button>
      </div>
    </div>
  );
}

function PriceInput({
  value,
  label,
  unit,
  onChange,
}: {
  value: string;
  label: string;
  unit: string;
  onChange: (value: string) => void;
}): ReactNode {
  return (
    <InputGroup className="w-36">
      <InputGroupInput
        inputMode="decimal"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="0.00"
        aria-label={label}
      />
      <InputGroupAddon align="inline-end">{unit}</InputGroupAddon>
    </InputGroup>
  );
}
