import { Fragment, useId, useMemo, useState, type MouseEvent } from "react";
import { Check, ChevronDown, ChevronRight, ExternalLink, Search, Store, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import { endpointSettingsPath, marketSettingsPath } from "@/app-routes";
import { ResolutionPicker } from "@/components/shared/ResolutionPicker";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Toggle } from "@/components/ui/toggle";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type { DiscoveryFormat, MatchedCatalogEntry, MediaType } from "@/types";
import { parseMultiplier } from "@/utils/multiplier";
import { IMAGE_STANDARD_RESOLUTIONS, VIDEO_STANDARD_RESOLUTIONS, resolutionPlaceholder } from "@/utils/provider-models";
import { CapabilityOverrideRow } from "./CapabilityOverrideRow";
import { durationsError } from "./durationErrors";
import { FieldHint, FieldRow } from "./CustomProviderFieldRow";
import { isComfyuiProtocol, isComfyuiVideoEndpoint, withLastFrameOverride } from "./customProviderHelpers";
import { matchedEntryFor, type ModelRow } from "./customProviderFormState";
import { EndpointSelect } from "./EndpointSelect";
import { RequestAddresses } from "./RequestAddresses";

//: Show search/media filters above this model count.
const FILTER_THRESHOLD = 5;

const MEDIA_LABEL_KEY: Record<MediaType, string> = {
  text: "media_type_text",
  image: "media_type_image",
  video: "media_type_video",
  audio: "media_type_audio",
};
const MEDIA_ORDER = Object.keys(MEDIA_LABEL_KEY) as MediaType[];

type MediaFilter = "all" | MediaType;

// Three mutually exclusive empty-tier explanations; both flags false means readable frame rates yielding no whole-second durations.
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

/** Three matching-entry states: unknown (unsaved new rows), unpriced (no matching entry or price table), priced. */
function entryState(entry: MatchedCatalogEntry | null | undefined): "pending" | "unpriced" | "priced" {
  if (entry === undefined) return "pending";
  return entry?.priced ? "priced" : "unpriced";
}

/** Matching-entry table cells: priced shows entry names, other states short labels. */
function EntryCell({ row }: { row: ModelRow }) {
  const { t } = useTranslation("dashboard");
  const entry = matchedEntryFor(row);
  const multiplier = parseMultiplier(row.multiplier_text);
  const state = entryState(entry);
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      {state === "priced" && entry ? (
        <TruncatedText text={entry.display_name} className="text-subtle-foreground" />
      ) : state === "unpriced" ? (
        <span className="truncate text-warn">{t("cp_model_entry_unpriced")}</span>
      ) : (
        <span className="truncate text-muted-foreground">{t("cp_model_entry_pending")}</span>
      )}
      {multiplier !== undefined && multiplier !== 1 && (
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
          <span className="sr-only">{t("cp_model_multiplier_label")} </span>×{multiplier}
        </span>
      )}
    </div>
  );
}

export interface ModelTableProps {
  /** Model rows with endpoints derived for current protocols. */
  rows: ModelRow[];
  protocol: DiscoveryFormat;
  /** Current form API addresses, used to construct complete request addresses. */
  baseUrl: string;
  expanded: ReadonlySet<string>;
  onToggleExpanded: (key: string) => void;
  onUpdate: (key: string, patch: Partial<ModelRow>) => void;
  onRemove: (key: string) => void;
  onToggleDefault: (key: string) => void;
  onSetEnabled: (keys: string[], enabled: boolean) => void;
}

/**
 * Custom-provider model tables: enabled, model ID, type, endpoint, matching entry and default columns. Click rows to expand editors below,
 * permitting multiple expanded rows; above five models, show search/media filters.
 */
export function ModelTable({
  rows,
  protocol,
  baseUrl,
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
  // Filters apply only while visible: at five or fewer models, controls disappear and residual filters must not hide rows.
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
            <col className="w-44" />
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
              <TableHead>{t("cp_model_entry_column")}</TableHead>
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
              const name = row.model_id || t("cp_model_id_missing");
              const editorId = `cp-model-editor-${row.key}`;
              // Row clicks expand/collapse; inline checkboxes/buttons/inputs perform their own actions without toggling.
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
                      <EntryCell row={row} />
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
                            baseUrl={baseUrl}
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
  baseUrl: string;
  onUpdate: (patch: Partial<ModelRow>) => void;
  onRemove: () => void;
}

/** Expanded editors share attribute label columns, with Delete model at bottom. */
function ModelFields({ row, protocol, baseUrl, onUpdate, onRemove }: ModelFieldsProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const requests = useEndpointCatalogStore((s) => s.endpoints.find((e) => e.key === row.endpoint)?.requests);
  const media = useEndpointCatalogStore((s) => s.endpointToMediaType[row.endpoint]);
  const constraints = useEndpointCatalogStore((s) => s.endpointConstraints[row.endpoint]);
  const endImageCapable = useEndpointCatalogStore((s) => s.endpointToEndImageCapable[row.endpoint] ?? false);
  const descriptor = useEndpointCatalogStore((s) => s.endpoints.find((e) => e.key === row.endpoint));
  // Only ComfyUI video rows edit durations; other durations follow entries, retaining legacy overrides unchanged.
  const editsDurations = descriptor !== undefined && isComfyuiVideoEndpoint(descriptor);
  const idPrefix = `cp-model-${row.key}`;
  const tierEmpty = constraints?.durationTierEmpty ?? false;
  const emptyCopy =
    EMPTY_TIER_COPY[
      constraints?.durationFixed ? "fixed" : constraints?.durationFrameRateMissing ? "frameRateMissing" : "notDerivable"
    ];
  const durationError = tierEmpty ? null : durationsError(t, row.durations_text);
  const multiplierInvalid = parseMultiplier(row.multiplier_text) === undefined;

  return (
    <div className="flex flex-col gap-3">
      <FieldRow label={t("model_id_label")} htmlFor={`${idPrefix}-id`}>
        <Input
          mono
          id={`${idPrefix}-id`}
          value={row.model_id}
          autoComplete="off"
          spellCheck={false}
          placeholder={t("cp_model_id_placeholder")}
          onChange={(e) => {
            const nextId = e.target.value;
            // Capability validity binds to (endpoint, model_id); changed model IDs invalidate overrides.
            onUpdate({ model_id: nextId, capability_overrides: null });
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
              // Override validity follows endpoints (last_frame requires ending-frame support); request overrides apply only to original endpoints:
              // endpoint changes invalidate both.
              capability_overrides: null,
              path_overrides: null,
            })
          }
        />
        <div className="flex flex-wrap items-center gap-x-4">
          {row.endpoint && (
            <Link
              href={endpointSettingsPath(row.endpoint)}
              className={buttonVariants({ variant: "link", size: "sm" })}
            >
              <ExternalLink aria-hidden data-icon="inline-start" />
              {t("cp_open_endpoint")}
            </Link>
          )}
          <Link href={marketSettingsPath("browse", { media })} className={buttonVariants({ variant: "link", size: "sm" })}>
            <Store aria-hidden data-icon="inline-start" />
            {t("cp_endpoint_from_market")}
          </Link>
        </div>
      </FieldRow>

      {requests && requests.length > 0 && (
        <FieldRow label={t("cp_request_address")} group>
          <RequestAddresses
            requests={requests}
            baseUrl={baseUrl}
            modelId={row.model_id}
            overrides={row.path_overrides}
            onChange={(next) => onUpdate({ path_overrides: next })}
          />
        </FieldRow>
      )}

      <FieldRow label={t("cp_model_entry_column")} hint={<EntryHint entry={matchedEntryFor(row)} />}>
        <EntryDetail entry={matchedEntryFor(row)} />
      </FieldRow>

      <FieldRow
        label={t("cp_model_multiplier_label")}
        htmlFor={`${idPrefix}-multiplier`}
        hint={
          multiplierInvalid ? (
            <FieldHint tone="warn">{t("cp_multiplier_invalid")}</FieldHint>
          ) : (
            <FieldHint>{t("cp_model_multiplier_help")}</FieldHint>
          )
        }
      >
        <Input
          id={`${idPrefix}-multiplier`}
          inputMode="decimal"
          autoComplete="off"
          className="w-32"
          value={row.multiplier_text}
          onChange={(e) => onUpdate({ multiplier_text: e.target.value })}
          aria-invalid={multiplierInvalid}
        />
      </FieldRow>

      {/* Resolution: image/video only; audio lacks this dimension. */}
      {(media === "image" || media === "video") && (
        <FieldRow
          label={t("resolution_label")}
          // Disabled reasons require visible explanations; title is inaccessible to keyboards/touch.
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

      {/* ComfyUI video durations write capability durations overrides; blank follows native workflows. */}
      {editsDurations && (
        <FieldRow
          label={t("supported_durations_label")}
          htmlFor={`${idPrefix}-durations`}
          hint={
            // Disabled reasons require visible explanations. Missing frame-rate sources are fixable, so say where to repair;
            // unconvertible whole-second durations lack missing rates to supply; never promise a single repair restores them.
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
            value={row.durations_text}
            onChange={(e) => onUpdate({ durations_text: e.target.value })}
            placeholder={t(tierEmpty ? emptyCopy.placeholder : "supported_durations_placeholder")}
            disabled={tierEmpty}
            aria-invalid={durationError !== null}
          />
        </FieldRow>
      )}

      {/* Capability overrides: video endpoints only, initially last_frame. ComfyUI capabilities derive solely from bindings;
          servers reject all protocol override writes with 422, so expose no controls. */}
      {media === "video" && !isComfyuiProtocol(protocol) && (
        <FieldRow label={t("cap_override_last_frame_label")}>
          <CapabilityOverrideRow
            override={row.capability_overrides?.last_frame}
            // Backend system decisions require (endpoint, model_id); new rows do not have them yet.
            systemValue={null}
            endImageCapable={endImageCapable}
            onChange={(next) => onUpdate({ capability_overrides: withLastFrameOverride(row.capability_overrides, next) })}
          />
        </FieldRow>
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

/** Matching entries in editors: matched rows show names and vendor/official model names. */
function EntryDetail({ entry }: { entry: MatchedCatalogEntry | null | undefined }) {
  const { t } = useTranslation("dashboard");
  if (entry === undefined) return <p className="pt-1.5 text-sm text-muted-foreground">{t("cp_model_entry_pending")}</p>;
  if (entry === null) return <p className="pt-1.5 text-sm text-warn">{t("cp_model_entry_unpriced")}</p>;
  return (
    <p className="flex min-w-0 flex-wrap items-baseline gap-x-2 pt-1.5 text-sm">
      <span>{entry.display_name}</span>
      <span className="font-mono text-xs break-all text-muted-foreground">
        {entry.maker}/{entry.name}
      </span>
    </p>
  );
}

/** Below-entry explanations: unpriced explains billing; unknown explains when results become available. */
function EntryHint({ entry }: { entry: MatchedCatalogEntry | null | undefined }) {
  const { t } = useTranslation("dashboard");
  switch (entryState(entry)) {
    case "pending":
      return <FieldHint>{t("cp_model_entry_pending_hint")}</FieldHint>;
    case "unpriced":
      return (
        <FieldHint tone="warn">
          {t(entry === null ? "cp_model_entry_unmatched_hint" : "cp_model_entry_no_price_hint")}
        </FieldHint>
      );
    default:
      return null;
  }
}
