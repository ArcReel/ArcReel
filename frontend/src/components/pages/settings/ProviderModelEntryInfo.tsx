import { useEffect, useId, useState, type ReactNode } from "react";
import { Link2, Loader2 } from "lucide-react";
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
import type { CapabilityKey, ProviderModelDetail, ProviderModelEntryInfo, ProviderModelItem } from "@/types";
import { errMsg } from "@/utils/async";
import { FieldHint, FieldRow } from "./CustomProviderFieldRow";
import { CapabilityValueEditor, capabilityValueText, withoutDroppedResolutions } from "./ProviderCapabilityOverrides";
import { MEDIA_LABEL_KEY, priceSummaryText } from "./providerModelDisplay";

/** 「模型信息」可以修改的部分。 */
export interface EntryDraft {
  display_name: string;
  facts: Record<string, unknown>;
}

export function entryDraftOf(entry: ProviderModelEntryInfo | null): EntryDraft | null {
  return entry ? { display_name: entry.display_name, facts: entry.facts } : null;
}

/** 各媒体类型可以修改的模型事实，按界面顺序；时长联动约束紧跟可选时长。 */
const EDITABLE_FACTS: Record<ProviderModelItem["media_type"], CapabilityKey[]> = {
  text: ["image_input", "structured_output", "max_output_tokens"],
  image: ["text_to_image", "image_to_image", "resolutions"],
  video: ["durations", "duration_constraints", "resolutions"],
  audio: [],
};

/** 「模型信息」里填写不正确的字段，在 Sheet 的校验集合里与上段的键区分开。 */
export const ENTRY_INVALID_PREFIX = "entry:";

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
  /** 需传稳定引用：字段重新挂载时据此清掉上一代的「填写不正确」。 */
  onValidityChange: (key: string, valid: boolean) => void;
  /** 恢复官方数据之后重新读取 Sheet 内容。 */
  onRestored: () => Promise<void>;
}

/**
 * Sheet 下段「模型信息」：对应到的模型条目，所有使用它的供应商共用。显示名与模型事实可以修改，与上段共用保存栏；
 * 厂商、官方模型名、媒体类型、别名只读。改过的条目在字段下方显示官方值并可逐项采用，「恢复官方数据」立即生效。
 */
export function ProviderModelEntryInfo({ detail, value, onChange, onValidityChange, onRestored }: Props) {
  const { t } = useTranslation("dashboard");
  const headingId = useId();
  const idPrefix = useId();
  const entry = detail.entry;
  const price = detail.model.price ? priceSummaryText(t, detail.model.price) : "";

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
      <h3 id={headingId} className="text-base font-medium">
        {t("pm_sheet_model_info")}
      </h3>
      {entry === null || value === null ? (
        <p className="rounded-lg border border-dashed border-warn/50 px-4 py-3 text-sm">{t("pm_sheet_no_entry")}</p>
      ) : (
        <>
          <div className="-mt-2 flex flex-col gap-2">
            <SourceNote entry={entry} onRestored={onRestored} />
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

          <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
            <InfoRow label={t("pm_info_price")}>{price || t("pm_info_price_none")}</InfoRow>
          </dl>
        </>
      )}
    </section>
  );
}

interface FactFieldProps {
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
function FactField({ dimension, value, resolutions, onChange, validityKey, onValidityChange, official }: FactFieldProps) {
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

/** 维护说明一行：官方维护 / 我改过（可恢复官方数据）/ 官方没有、由我填写。 */
function SourceNote({ entry, onRestored }: { entry: ProviderModelEntryInfo; onRestored: () => Promise<void> }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [open, setOpen] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (entry.source === "official") {
    return <p className="text-xs text-muted-foreground">{t("pm_entry_source_official")}</p>;
  }
  if (entry.source === "user_created") {
    return <p className="text-xs text-muted-foreground">{t("pm_entry_source_user_created")}</p>;
  }

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

  const title = t("pm_entry_restore_title");
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-background/60 px-3 py-2">
      <span className="min-w-0 flex-1 text-xs text-subtle-foreground">{t("pm_entry_source_modified")}</span>
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        {t("pm_entry_restore")}
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
                {t("pm_entry_restore_description", { name: entry.display_name })}
                {entry.shared_with.length > 0 && t("pm_entry_restore_shared", { providers: providerNames(t, entry) })}
              </AlertDialogDescription>
              {error && (
                <p role="alert" className="text-sm wrap-break-word text-destructive">
                  {t("pm_entry_restore_failed", { message: error })}
                </p>
              )}
            </div>
          </AlertDialogBody>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={restoring}>{t("common:cancel")}</AlertDialogCancel>
            <AlertDialogAction disabled={restoring} onClick={() => void handleRestore()}>
              {restoring && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
              {t("pm_entry_restore")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
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
