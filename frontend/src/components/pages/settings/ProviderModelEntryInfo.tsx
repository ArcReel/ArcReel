import { useEffect, useId, useState, type ReactNode } from "react";
import { ArrowLeftRight, Link2, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { API } from "@/api";
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
import { Input } from "@/components/ui/input";
import { CatalogEntryPicker } from "@/components/shared/CatalogEntryPicker";
import type {
  CapabilityKey,
  CatalogEntryHit,
  NewCatalogEntry,
  ProviderModelDetail,
  ProviderModelEntryInfo,
  ProviderModelItem,
} from "@/types";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { FieldHint, FieldRow } from "./CustomProviderFieldRow";
import { ManualEntryDialog } from "./ManualEntryDialog";
import { capabilityValueText, withoutDroppedResolutions } from "./capabilityValues";
import { CapabilityValueEditor } from "./ProviderCapabilityOverrides";
import { PriceEditor } from "./price-editor/PriceEditor";
import { isPriceTableValid, priceTableOf, type PriceTable } from "./price-editor/priceTable";
import { MEDIA_LABEL_KEY } from "./providerModelDisplay";
import { EDITABLE_FACTS, providerNames, type EntryDraft } from "./providerModelEntry";

/**
 * Entry changes persisted with saves, editing drafts only without immediate effect: Associate another model stores bindings; unlink restores name matching;
 * manual entry creates/binds entries. Pending changes show explanations below instead of original entry fields.
 */
export type PendingEntryChange =
  | { kind: "link"; entry: CatalogEntryHit }
  | { kind: "unlink" }
  | { kind: "create"; entry: NewCatalogEntry };

/** Invalid Model information fields use validation keys distinct from the preceding section in Sheets. */
export const ENTRY_INVALID_PREFIX = "entry:";
const PRICE_INVALID_KEY = `${ENTRY_INVALID_PREFIX}price`;

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function makerLabel(t: TFunction, maker: string): string {
  return t(`dashboard:catalog_maker_${maker}`, { defaultValue: maker });
}

interface Props {
  detail: ProviderModelDetail;
  value: EntryDraft | null;
  onChange: (next: EntryDraft) => void;
  /** User-maintained models may link other models or enter details manually; officially cataloged models stay officially maintained. */
  userOwned: boolean;
  /** Endpoint-determined model media types restrict links to matching entries and fix manual-entry types. */
  mediaType: ProviderModelItem["media_type"];
  pending: PendingEntryChange | null;
  onPendingChange: (next: PendingEntryChange | null) => void;
  /** Supply stable references to clear previous generations' invalid states during field remounts. */
  onValidityChange: (key: string, valid: boolean) => void;
  /** Reread Sheet contents after restoring official data. */
  onRestored: () => Promise<void>;
}

/**
 * Lower Sheet Model information: matching entries shared by every provider using them. Names/facts edit through shared save bars;
 * vendor/official name/media/aliases stay readonly. Modified entries show adoptable official values beneath fields; Restore official data applies immediately.
 */
export function ProviderModelEntryInfo({
  detail,
  value,
  onChange,
  userOwned,
  mediaType,
  pending,
  onPendingChange,
  onValidityChange,
  onRestored,
}: Props) {
  const { t } = useTranslation("dashboard");
  const [dialog, setDialog] = useState<"pick" | "manual" | null>(null);
  const [mismatch, setMismatch] = useState(false);
  const headingId = useId();
  const idPrefix = useId();
  const entry = detail.entry;

  const setFact = (key: CapabilityKey, next: unknown) => {
    if (!value) return;
    const facts: Record<string, unknown> = { ...value.facts, [key]: next };
    // Removed resolutions disappear from duration-linked constraints.
    if (key === "resolutions" && Array.isArray(next) && facts.duration_constraints) {
      facts.duration_constraints = withoutDroppedResolutions(facts.duration_constraints, next as string[]);
    }
    onChange({ ...value, facts });
  };

  return (
    // Separate from upper sections: pale backgrounds span Sheet width, offsetting SheetBody horizontal/bottom padding.
    <section
      aria-labelledby={headingId}
      className="-mx-5 -mb-4 flex flex-col gap-4 border-t border-border bg-muted/30 px-5 pt-5 pb-6"
    >
      <div className="flex items-center gap-2">
        <h3 id={headingId} className="min-w-0 flex-1 text-base font-medium">
          {t("pm_sheet_model_info")}
        </h3>
        {userOwned && (entry !== null || pending !== null) && (
          <Button
            variant="ghost"
            size="xs"
            onClick={() => {
              setMismatch(false);
              setDialog("pick");
            }}
          >
            <ArrowLeftRight aria-hidden data-icon="inline-start" />
            {t("pm_link_other")}
          </Button>
        )}
      </div>
      {mismatch && (
        <p role="alert" className="-mt-2 text-xs text-warn">
          {t("pm_link_media_mismatch", { type: t(MEDIA_LABEL_KEY[mediaType]) })}
        </p>
      )}
      {pending !== null ? (
        <PendingChange
          pending={pending}
          onEdit={() => setDialog("manual")}
          onCancel={() => onPendingChange(null)}
        />
      ) : entry === null || value === null ? (
        <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed border-warn/50 px-4 py-3">
          <p className="text-sm">{t("pm_sheet_no_entry")}</p>
          {userOwned && (
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                onClick={() => {
                  setMismatch(false);
                  setDialog("pick");
                }}
              >
                {t("pm_link_pick")}
              </Button>
              <Button size="sm" variant="outline" onClick={() => setDialog("manual")}>
                {t("pm_link_manual")}
              </Button>
            </div>
          )}
          {userOwned && detail.entry_binding && (
            <BoundNote binding={detail.entry_binding} onUnlink={() => onPendingChange({ kind: "unlink" })} />
          )}
        </div>
      ) : (
        <>
          <div className="-mt-2 flex flex-col gap-2">
            <SourceNote entry={entry} onRestored={onRestored} />
            {userOwned && detail.entry_binding && (
              <BoundNote
                binding={detail.entry_binding}
                displayName={entry.display_name}
                onUnlink={() => onPendingChange({ kind: "unlink" })}
              />
            )}
            {entry.shared_with.length > 0 && (
              <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
                <Link2 aria-hidden className="mt-0.5 size-3.5 shrink-0" />
                {t("pm_entry_shared", { providers: providerNames(t, entry) })}
              </p>
            )}
          </div>

          <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
            <InfoRow label={t("pm_info_maker")}>{makerLabel(t, entry.maker)}</InfoRow>
            <InfoRow label={t("pm_info_official_name")}>
              <span className="font-mono">{entry.name}</span>
            </InfoRow>
            <InfoRow label={t("pm_info_media_type")}>{t(MEDIA_LABEL_KEY[entry.media_type])}</InfoRow>
            {entry.aliases.length > 0 && (
              <InfoRow label={t("pm_info_aliases")}>
                <span className="font-mono">{entry.aliases.join(t("pm_cap_value_list_separator"))}</span>
              </InfoRow>
            )}
            {entry.context_length !== null && (
              <InfoRow label={t("pm_info_context_length")}>
                {t("pm_info_token_count", { value: entry.context_length.toLocaleString() })}
              </InfoRow>
            )}
          </dl>

          <FieldRow
            label={t("pm_info_display_name")}
            htmlFor={`${idPrefix}-display-name`}
            hint={
              <>
                {!value.display_name.trim() && <FieldHint tone="warn">{t("pm_entry_display_name_required")}</FieldHint>}
                {entry.official && value.display_name !== entry.official.display_name && (
                  <OfficialValue
                    text={entry.official.display_name}
                    label={t("pm_info_display_name")}
                    onAdopt={() => {
                      const official = entry.official;
                      if (official) onChange({ ...value, display_name: official.display_name });
                    }}
                  />
                )}
              </>
            }
          >
            <Input
              id={`${idPrefix}-display-name`}
              autoComplete="off"
              value={value.display_name}
              aria-invalid={!value.display_name.trim()}
              onChange={(e) => onChange({ ...value, display_name: e.target.value })}
            />
          </FieldRow>

          {EDITABLE_FACTS[entry.media_type].map((key) => {
            const officialValue = entry.official?.facts[key];
            const differs = entry.official !== null && !sameJson(value.facts[key] ?? null, officialValue ?? null);
            return (
              <FactField
                key={key}
                dimension={key}
                value={value.facts[key]}
                resolutions={Array.isArray(value.facts.resolutions) ? (value.facts.resolutions as string[]) : []}
                onChange={(next) => setFact(key, next)}
                validityKey={`${ENTRY_INVALID_PREFIX}${key}`}
                onValidityChange={onValidityChange}
                official={
                  differs ? (
                    <OfficialValue
                      text={capabilityValueText(t, key, officialValue)}
                      label={t(`pm_cap_${key}`)}
                      onAdopt={() => setFact(key, structuredClone(officialValue ?? null))}
                    />
                  ) : null
                }
              />
            );
          })}

          <EntryPrice
            entry={entry}
            value={value.price}
            resolutions={Array.isArray(value.facts.resolutions) ? (value.facts.resolutions as string[]) : []}
            onChange={(next) => onChange({ ...value, price: next })}
            onValidityChange={onValidityChange}
          />
        </>
      )}
      <CatalogEntryPicker
        open={dialog === "pick"}
        onOpenChange={(open) => !open && setDialog(null)}
        mediaType={mediaType}
        onSelect={(hit) => {
          if (hit.media_type !== mediaType) {
            setMismatch(true);
            return;
          }
          const alreadyBound = detail.entry_binding === `${hit.maker}/${hit.name}`;
          onPendingChange(alreadyBound ? null : { kind: "link", entry: hit });
        }}
      />
      {dialog === "manual" && (
        <ManualEntryDialog
          open
          onOpenChange={(open) => !open && setDialog(null)}
          mediaType={mediaType}
          initialName={detail.model.model_id}
          initial={pending?.kind === "create" ? pending.entry : null}
          onConfirm={(next) => onPendingChange({ kind: "create", entry: next })}
          onUseExisting={(hit) => onPendingChange({ kind: "link", entry: hit })}
        />
      )}
    </section>
  );
}

/** Price sections use editors; incomplete or duplicate-condition tiers make save bars block saving. */
function EntryPrice({
  entry,
  value,
  resolutions,
  onChange,
  onValidityChange,
}: {
  entry: ProviderModelEntryInfo;
  value: PriceTable;
  resolutions: string[];
  onChange: (next: PriceTable) => void;
  onValidityChange: (key: string, valid: boolean) => void;
}) {
  const { t } = useTranslation("dashboard");
  const headingId = useId();
  const valid = isPriceTableValid(value);
  useEffect(() => {
    onValidityChange(PRICE_INVALID_KEY, valid);
    return () => onValidityChange(PRICE_INVALID_KEY, true);
  }, [valid, onValidityChange]);

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2">
      <h4 id={headingId} className="text-sm text-muted-foreground">
        {t("pm_info_price")}
      </h4>
      <PriceEditor
        value={value}
        onChange={onChange}
        mediaType={entry.media_type}
        resolutions={resolutions}
        official={entry.official ? priceTableOf(entry.official.price) : null}
      />
    </section>
  );
}

export interface FactFieldProps {
  dimension: CapabilityKey;
  value: unknown;
  resolutions: string[];
  onChange: (next: unknown) => void;
  validityKey: string;
  onValidityChange: (key: string, valid: boolean) => void;
  official: ReactNode;
}

/**
 * One model fact. Editor text initializes only at mount; external changes (discard/adopt/restore official values) change generations
 * and remount; prior inputs withdraw their invalid states on unmount.
 */
export function FactField({ dimension, value, resolutions, onChange, validityKey, onValidityChange, official }: FactFieldProps) {
  const { t } = useTranslation("dashboard");
  const [seen, setSeen] = useState({ value, generation: 0 });
  if (!sameJson(seen.value, value)) setSeen({ value, generation: seen.generation + 1 });
  const generation = seen.generation;

  const label = t(`pm_cap_${dimension}`);
  return (
    <FieldRow label={label} group hint={official}>
      <CapabilityValueEditor
        key={generation}
        dimension={dimension}
        label={label}
        value={value}
        resolutions={resolutions}
        onChange={(next) => {
          setSeen((prev) => ({ ...prev, value: next }));
          onChange(next);
        }}
        onValidityChange={(valid) => onValidityChange(validityKey, valid)}
      />
    </FieldRow>
  );
}

/** Modified entries show adoptable official values beneath fields. */
function OfficialValue({ text, label, onAdopt }: { text: string; label: string; onAdopt: () => void }) {
  const { t } = useTranslation("dashboard");
  return (
    <p className="flex flex-wrap items-center gap-x-1 text-xs text-muted-foreground">
      <span className="min-w-0 wrap-break-word">
        {t("pm_entry_official_value")}
        <span className="text-subtle-foreground">{text}</span>
      </span>
      <Button
        variant="link"
        size="xs"
        aria-label={t("pm_entry_adopt_named", { name: label })}
        onClick={onAdopt}
      >
        {t("pm_entry_adopt")}
      </Button>
    </p>
  );
}

/** Maintenance rows: official / modified (restorable) / user-created (can adopt data after official cataloging). */
function SourceNote({ entry, onRestored }: { entry: ProviderModelEntryInfo; onRestored: () => Promise<void> }) {
  const { t } = useTranslation("dashboard");
  if (entry.source === "official") {
    return <p className="text-xs text-muted-foreground">{t("pm_entry_source_official")}</p>;
  }
  if (entry.source === "user_created") {
    // Later official cataloging never automatically merges identical models; users choose whether to adopt.
    if (entry.official === null) {
      return <p className="text-xs text-muted-foreground">{t("pm_entry_source_user_created")}</p>;
    }
    return (
      <DiscardCopy
        entry={entry}
        onRestored={onRestored}
        text={t("pm_entry_source_user_created_official")}
        action={t("pm_entry_use_official")}
        title={t("pm_entry_use_official_title")}
        description={t("pm_entry_use_official_description", { name: entry.display_name })}
        sharedText={(providers) => t("pm_entry_use_official_shared", { providers })}
        failed={(message) => t("pm_entry_use_official_failed", { message })}
      />
    );
  }
  return (
    <DiscardCopy
      entry={entry}
      onRestored={onRestored}
      text={t("pm_entry_source_modified")}
      action={t("pm_entry_restore")}
      title={t("pm_entry_restore_title")}
      description={t("pm_entry_restore_description", { name: entry.display_name })}
      sharedText={(providers) => t("pm_entry_restore_shared", { providers })}
      failed={(message) => t("pm_entry_restore_failed", { message })}
    />
  );
}

interface DiscardCopyProps {
  entry: ProviderModelEntryInfo;
  onRestored: () => Promise<void>;
  text: string;
  action: string;
  title: string;
  description: string;
  sharedText: (providers: string) => string;
  failed: (message: string) => string;
}

/** Discard user copies/restore official data: Restore and Switch to official share APIs, confirming first and applying immediately. */
function DiscardCopy({ entry, onRestored, text, action, title, description, sharedText, failed }: DiscardCopyProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const pushToast = useAppStore((s) => s.pushToast);
  const [open, setOpen] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleRestore = async () => {
    setRestoring(true);
    setError(null);
    try {
      await API.restoreCatalogEntry({ maker: entry.maker, name: entry.name, media_type: entry.media_type });
    } catch (err) {
      setError(errMsg(err));
      setRestoring(false);
      return;
    }
    // Already applied: refresh failures notify refresh only, preventing users believing restoration failed and retrying.
    setOpen(false);
    setRestoring(false);
    try {
      await onRestored();
    } catch {
      pushToast(t("pm_entry_discard_refresh_failed"), "warning");
    }
  };

  return (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-background/60 px-3 py-2">
      <span className="min-w-0 flex-1 text-xs text-subtle-foreground">{text}</span>
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        {action}
      </Button>
      <AlertDialog
        open={open}
        onOpenChange={(next) => {
          if (!next && !restoring) setOpen(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{title}</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogBody tabIndex={0} role="region" aria-label={title}>
            <div className="flex flex-col gap-2">
              <AlertDialogDescription>
                {description}
                {entry.shared_with.length > 0 && sharedText(providerNames(t, entry))}
              </AlertDialogDescription>
              {error && (
                <p role="alert" className="text-sm wrap-break-word text-destructive">
                  {failed(error)}
                </p>
              )}
            </div>
          </AlertDialogBody>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={restoring}>{t("common:cancel")}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" disabled={restoring} onClick={() => void handleRestore()}>
              {restoring && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
              {action}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/**
 * Explicit entry bindings stop name matching and allow unlinking. Existing linked entries show display names; missing catalog entries
 * lack names and fall back to vendor/official-model names.
 */
function BoundNote({
  binding,
  displayName,
  onUnlink,
}: {
  binding: string;
  displayName?: string;
  onUnlink: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const [maker, ...rest] = binding.split("/");
  return (
    <p className="flex flex-wrap items-center gap-x-1 text-xs text-muted-foreground">
      <span className="min-w-0 wrap-break-word">
        {t("pm_link_bound", { name: displayName ?? `${makerLabel(t, maker ?? "")}/${rest.join("/")}` })}
      </span>
      <Button variant="link" size="xs" onClick={onUnlink}>
        {t("pm_link_unlink")}
      </Button>
    </p>
  );
}

/** Unsaved entry-change explanations: what saving will do, and discard actions. */
function PendingChange({
  pending,
  onEdit,
  onCancel,
}: {
  pending: PendingEntryChange;
  onEdit: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const actions = (
    <div className="flex gap-2">
      {pending.kind === "create" && (
        <Button size="sm" variant="outline" onClick={onEdit}>
          {t("pm_link_edit_manual")}
        </Button>
      )}
      <Button size="sm" variant="ghost" onClick={onCancel}>
        {t("pm_link_discard")}
      </Button>
    </div>
  );
  if (pending.kind === "unlink") {
    return (
      <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed border-border px-4 py-3">
        <p className="text-sm">{t("pm_link_pending_unlink")}</p>
        {actions}
      </div>
    );
  }
  if (pending.kind === "link") {
    const { entry } = pending;
    return (
      <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed border-primary/50 px-4 py-3">
        <p className="text-sm">{t("pm_link_pending", { name: entry.display_name })}</p>
        <p className="text-xs text-muted-foreground">
          {makerLabel(t, entry.maker)} · {t(MEDIA_LABEL_KEY[entry.media_type])}
        </p>
        {actions}
      </div>
    );
  }
  const { entry } = pending;
  const facts = EDITABLE_FACTS[entry.media_type].filter((key) => entry.facts?.[key] !== undefined);
  return (
    <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed border-primary/50 px-4 py-3">
      <p className="text-sm">{t("pm_manual_pending", { name: entry.display_name ?? entry.name })}</p>
      <dl className="grid w-full grid-cols-[7rem_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
        <InfoRow label={t("pm_info_maker")}>{entry.maker}</InfoRow>
        <InfoRow label={t("pm_info_official_name")}>
          <span className="font-mono">{entry.name}</span>
        </InfoRow>
        {facts.map((key) => (
          <InfoRow key={key} label={t(`pm_cap_${key}`)}>
            {capabilityValueText(t, key, entry.facts?.[key])}
          </InfoRow>
        ))}
        <InfoRow label={t("pm_info_price")}>{entry.price ? t("pm_manual_price_filled") : t("pm_manual_price_empty")}</InfoRow>
      </dl>
      {actions}
    </div>
  );
}

function InfoRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 wrap-break-word">{children}</dd>
    </>
  );
}
