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
import { errMsg } from "@/utils/async";
import { FieldHint, FieldRow } from "./CustomProviderFieldRow";
import { ManualEntryDialog } from "./ManualEntryDialog";
import { CapabilityValueEditor, capabilityValueText, withoutDroppedResolutions } from "./ProviderCapabilityOverrides";
import { PriceEditor } from "./price-editor/PriceEditor";
import { isPriceTableValid, priceTableOf, type PriceTable } from "./price-editor/priceTable";
import { MEDIA_LABEL_KEY } from "./providerModelDisplay";

/** 「模型信息」可以修改的部分。 */
export interface EntryDraft {
  display_name: string;
  facts: Record<string, unknown>;
  /** 编辑中的价目表；没有计费项就是「不填价格」。 */
  price: PriceTable;
}

/**
 * 随保存一起写入的条目改动，只改草稿、不立即生效：「关联其他模型」存为条目绑定，「解除关联」改回按名字对应，
 * 「手动填写」新建条目并绑定到它。有改动时，下段显示改动的说明而不是原条目的字段。
 */
export type PendingEntryChange =
  | { kind: "link"; entry: CatalogEntryHit }
  | { kind: "unlink" }
  | { kind: "create"; entry: NewCatalogEntry };

export function entryDraftOf(entry: ProviderModelEntryInfo | null): EntryDraft | null {
  return entry ? { display_name: entry.display_name, facts: entry.facts, price: priceTableOf(entry.price) } : null;
}

/** 各媒体类型可以修改的模型事实，按界面顺序；时长联动约束紧跟可选时长。 */
export const EDITABLE_FACTS: Record<ProviderModelItem["media_type"], CapabilityKey[]> = {
  text: ["image_input", "structured_output", "max_output_tokens"],
  image: ["text_to_image", "image_to_image", "resolutions"],
  video: ["durations", "duration_constraints", "resolutions"],
  audio: [],
};

/** 「模型信息」里填写不正确的字段，在 Sheet 的校验集合里与上段的键区分开。 */
export const ENTRY_INVALID_PREFIX = "entry:";
const PRICE_INVALID_KEY = `${ENTRY_INVALID_PREFIX}price`;

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function makerLabel(t: TFunction, maker: string): string {
  return t(`dashboard:catalog_maker_${maker}`, { defaultValue: maker });
}

/** 供应商名列表，如「『A』『B』」。 */
export function providerNames(t: TFunction, entry: ProviderModelEntryInfo): string {
  return entry.shared_with
    .map((p) => t("pm_entry_provider_name", { name: p.display_name }))
    .join(t("pm_entry_provider_separator"));
}

interface Props {
  detail: ProviderModelDetail;
  value: EntryDraft | null;
  onChange: (next: EntryDraft) => void;
  /** 用户负责的模型可以关联其他模型或手动填写；官方收录的模型由官方维护。 */
  userOwned: boolean;
  /** 模型的媒体类型（调用端点决定）：关联只能选同类型的条目，手动填写固定为这个类型。 */
  mediaType: ProviderModelItem["media_type"];
  pending: PendingEntryChange | null;
  onPendingChange: (next: PendingEntryChange | null) => void;
  /** 需传稳定引用：字段重新挂载时据此清掉上一代的「填写不正确」。 */
  onValidityChange: (key: string, valid: boolean) => void;
  /** 恢复官方数据之后重新读取 Sheet 内容。 */
  onRestored: () => Promise<void>;
}

/**
 * Sheet 下段「模型信息」：对应到的模型条目，所有使用它的供应商共用。显示名与模型事实可以修改，与上段共用保存栏；
 * 厂商、官方模型名、媒体类型、别名只读。改过的条目在字段下方显示官方值并可逐项采用，「恢复官方数据」立即生效。
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
    // 去掉的分辨率不再出现在时长联动约束里
    if (key === "resolutions" && Array.isArray(next) && facts.duration_constraints) {
      facts.duration_constraints = withoutDroppedResolutions(facts.duration_constraints, next as string[]);
    }
    onChange({ ...value, facts });
  };

  return (
    // 与上段分开：铺满 Sheet 宽度的浅底色，抵消 SheetBody 的左右与底部内边距
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
              <BoundNote binding={detail.entry_binding} onUnlink={() => onPendingChange({ kind: "unlink" })} />
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

/** 「价格」一段：价格编辑器；价目表有填写不完整或条件重复的档时，保存栏拦下保存。 */
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
 * 一项模型事实。编辑器里的文本只在挂载时取初值：取值被外部改掉（放弃修改、采用官方值、恢复官方数据）时换一代
 * 重新挂载，并清掉上一代留下的「填写不正确」。
 */
export function FactField({ dimension, value, resolutions, onChange, validityKey, onValidityChange, official }: FactFieldProps) {
  const { t } = useTranslation("dashboard");
  const [seen, setSeen] = useState({ value, generation: 0 });
  if (!sameJson(seen.value, value)) setSeen({ value, generation: seen.generation + 1 });
  const generation = seen.generation;
  useEffect(() => {
    if (generation > 0) onValidityChange(validityKey, true);
  }, [generation, validityKey, onValidityChange]);

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

/** 改过的条目在字段下方显示官方值，可以「采用」。 */
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

/** 维护说明一行：官方维护 / 我改过（可恢复官方数据）/ 我新建的（官方收录后可改用官方数据）。 */
function SourceNote({ entry, onRestored }: { entry: ProviderModelEntryInfo; onRestored: () => Promise<void> }) {
  const { t } = useTranslation("dashboard");
  if (entry.source === "official") {
    return <p className="text-xs text-muted-foreground">{t("pm_entry_source_official")}</p>;
  }
  if (entry.source === "user_created") {
    // 官方数据日后收录了同一个模型：不自动合并，由用户决定是否改用
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

/** 丢弃用户副本、回到官方数据：「恢复官方数据」与「改用官方数据」是同一个接口，先确认，确认后立即生效。 */
function DiscardCopy({ entry, onRestored, text, action, title, description, sharedText, failed }: DiscardCopyProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [open, setOpen] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleRestore = async () => {
    setRestoring(true);
    setError(null);
    try {
      await API.restoreCatalogEntry({ maker: entry.maker, name: entry.name, media_type: entry.media_type });
      await onRestored();
      setOpen(false);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setRestoring(false);
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
            <AlertDialogAction disabled={restoring} onClick={() => void handleRestore()}>
              {restoring && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
              {action}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/** 用户指定了条目绑定时的说明：这个模型不再按名字对应，可以解除。 */
function BoundNote({ binding, onUnlink }: { binding: string; onUnlink: () => void }) {
  const { t } = useTranslation("dashboard");
  const [maker, ...rest] = binding.split("/");
  return (
    <p className="flex flex-wrap items-center gap-x-1 text-xs text-muted-foreground">
      <span className="min-w-0 wrap-break-word">
        {t("pm_link_bound", { name: `${makerLabel(t, maker ?? "")}/${rest.join("/")}` })}
      </span>
      <Button variant="link" size="xs" onClick={onUnlink}>
        {t("pm_link_unlink")}
      </Button>
    </p>
  );
}

/** 还没保存的条目改动的说明：保存后会发生什么，以及放弃。 */
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
