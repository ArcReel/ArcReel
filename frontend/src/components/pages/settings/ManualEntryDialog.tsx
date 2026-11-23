import { useCallback, useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import type { CapabilityKey, CatalogEntryHit, NewCatalogEntry, ProviderModelItem } from "@/types";
import { FieldHint, FieldRow } from "./CustomProviderFieldRow";
import { FactField } from "./ProviderModelEntryInfo";
import { PriceEditor } from "./price-editor/PriceEditor";
import { isPriceTableValid, priceForSave, priceTableOf, type PriceTable } from "./price-editor/priceTable";
import { MEDIA_LABEL_KEY } from "./providerModelDisplay";
import { EDITABLE_FACTS } from "./providerModelEntry";

type MediaType = ProviderModelItem["media_type"];

const MEDIA_TYPES: readonly MediaType[] = ["text", "image", "video", "audio"];

const FILL_IN_FACTS = (media: MediaType): CapabilityKey[] => EDITABLE_FACTS[media].filter((key) => key !== "duration_constraints");

function defaultFacts(media: MediaType): Record<string, unknown> {
  switch (media) {
    case "text":
      return { image_input: false, structured_output: false };
    case "image":
      return { text_to_image: true, image_to_image: false, resolutions: [] };
    case "video":
      return { durations: [], resolutions: [] };
    default:
      return {};
  }
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mediaType?: MediaType;
  mediaTypes?: readonly string[];
  initialName?: string;
  initial?: NewCatalogEntry | null;
  onConfirm: (entry: NewCatalogEntry) => void;
  onUseExisting?: (hit: CatalogEntryHit) => void;
}

export function ManualEntryDialog({ open, onOpenChange, ...rest }: Props) {
  const { t } = useTranslation("dashboard");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{t("pm_manual_title")}</DialogTitle>
          <DialogDescription>{t("pm_manual_description")}</DialogDescription>
        </DialogHeader>
        <ManualEntryForm onClose={() => onOpenChange(false)} {...rest} />
      </DialogContent>
    </Dialog>
  );
}

function ManualEntryForm({
  mediaType,
  mediaTypes,
  initialName = "",
  initial,
  onConfirm,
  onUseExisting,
  onClose,
}: Omit<Props, "open" | "onOpenChange"> & { onClose: () => void }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const idPrefix = useId();
  const [maker, setMaker] = useState(initial?.maker ?? "");
  const [name, setName] = useState(initial?.name ?? initialName);
  const [displayName, setDisplayName] = useState(initial?.display_name ?? "");
  const options = MEDIA_TYPES.filter((type) => !mediaTypes?.length || mediaTypes.includes(type));
  const [media, setMedia] = useState<MediaType>(initial?.media_type ?? mediaType ?? options[0] ?? "text");
  const [facts, setFacts] = useState<Record<string, unknown>>(initial?.facts ?? defaultFacts(media));
  const [price, setPrice] = useState<PriceTable>(() => priceTableOf(initial?.price));
  const [durationsTouched, setDurationsTouched] = useState(initial !== undefined && initial !== null);
  const [invalidKeys, setInvalidKeys] = useState<ReadonlySet<string>>(new Set());
  const [lookup, setLookup] = useState<{ key: string; existing: CatalogEntryHit | null } | null>(null);

  const setValidity = useCallback((key: string, valid: boolean) => {
    setInvalidKeys((prev) => {
      if (prev.has(key) === !valid) return prev;
      const next = new Set(prev);
      if (valid) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const lookupName = useDebouncedValue(name.trim(), 250);
  useEffect(() => {
    if (!lookupName) return;
    const controller = new AbortController();
    API.getNewEntryHints({ name: lookupName, mediaType: media }, { signal: controller.signal })
      .then((hints) => {
        if (controller.signal.aborted) return;
        setLookup({ key: `${lookupName}|${media}`, existing: hints.existing });
        if (media === "video" && hints.durations && !durationsTouched) {
          setFacts((prev) => ({ ...prev, durations: hints.durations }));
        }
      })
      .catch(() => {
        // 提示只是辅助：查不到时不拦用户，保存时服务端仍会核对
      });
    return () => controller.abort();
  }, [lookupName, media, durationsTouched]);

  const switchMedia = (next: MediaType) => {
    setMedia(next);
    setFacts(defaultFacts(next));
    setDurationsTouched(false);
  };

  const trimmedMaker = maker.trim();
  const makerHasSlash = trimmedMaker.includes("/");
  const durations = Array.isArray(facts.durations) ? (facts.durations as number[]) : [];
  const existing = lookup?.key === `${name.trim()}|${media}` ? lookup.existing : null;
  const taken = existing !== null;
  const canConfirm =
    trimmedMaker !== "" &&
    !makerHasSlash &&
    name.trim() !== "" &&
    !taken &&
    invalidKeys.size === 0 &&
    isPriceTableValid(price) &&
    (media !== "video" || durations.length > 0);
  const resolutions = Array.isArray(facts.resolutions) ? (facts.resolutions as string[]) : [];

  return (
    <>
      <DialogBody>
        <div className="flex flex-col gap-4">
          <FieldRow
            label={t("pm_info_maker")}
            htmlFor={`${idPrefix}-maker`}
            hint={makerHasSlash ? <FieldHint tone="warn">{t("pm_manual_maker_slash")}</FieldHint> : <FieldHint>{t("pm_manual_maker_hint")}</FieldHint>}
          >
            <Input
              id={`${idPrefix}-maker`}
              autoComplete="off"
              spellCheck={false}
              value={maker}
              aria-invalid={makerHasSlash}
              onChange={(e) => setMaker(e.target.value)}
            />
          </FieldRow>

          <FieldRow
            label={t("pm_manual_name")}
            htmlFor={`${idPrefix}-name`}
            hint={
              taken && existing ? (
                <div className="flex flex-col items-start gap-0.5" role="alert">
                  <FieldHint tone="warn">{t("pm_manual_name_taken")}</FieldHint>
                  {onUseExisting && (
                    <Button
                      variant="link"
                      size="xs"
                      onClick={() => {
                        onUseExisting(existing);
                        onClose();
                      }}
                    >
                      {t("pm_manual_use_existing", { name: existing.display_name })}
                    </Button>
                  )}
                </div>
              ) : (
                <FieldHint>{t("pm_manual_name_hint")}</FieldHint>
              )
            }
          >
            <Input
              mono
              id={`${idPrefix}-name`}
              autoComplete="off"
              spellCheck={false}
              value={name}
              aria-invalid={taken}
              onChange={(e) => setName(e.target.value)}
            />
          </FieldRow>

          <FieldRow label={t("pm_info_display_name")} htmlFor={`${idPrefix}-display-name`}>
            <Input
              id={`${idPrefix}-display-name`}
              autoComplete="off"
              value={displayName}
              placeholder={name.trim() || t("pm_manual_display_name_placeholder")}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </FieldRow>

          <FieldRow label={t("pm_info_media_type")} group>
            {mediaType ? (
              <p className="pt-1.5 text-sm">{t(MEDIA_LABEL_KEY[mediaType])}</p>
            ) : (
              <ToggleGroup
                aria-label={t("pm_info_media_type")}
                variant="outline"
                size="sm"
                value={[media]}
                onValueChange={(next: string[]) => {
                  if (next[0] && next[0] !== media) switchMedia(next[0] as MediaType);
                }}
              >
                {options.map((type) => (
                  <ToggleGroupItem key={type} value={type}>
                    {t(MEDIA_LABEL_KEY[type])}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            )}
          </FieldRow>

          {FILL_IN_FACTS(media).map((key) => (
            <FactField
              key={`${media}:${key}`}
              dimension={key}
              value={facts[key]}
              resolutions={resolutions}
              onChange={(next) => {
                if (key === "durations") setDurationsTouched(true);
                setFacts((prev) => ({ ...prev, [key]: next }));
              }}
              validityKey={key}
              onValidityChange={setValidity}
              official={
                key === "durations" && !durationsTouched && durations.length > 0 ? (
                  <FieldHint>{t("pm_manual_durations_prefilled")}</FieldHint>
                ) : null
              }
            />
          ))}

          <FieldRow label={t("pm_info_price")}>
            <PriceEditor value={price} onChange={setPrice} mediaType={media} resolutions={resolutions} />
          </FieldRow>
        </div>
      </DialogBody>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          {t("common:cancel")}
        </Button>
        <Button
          disabled={!canConfirm}
          onClick={() => {
            const saved = priceForSave(price);
            onConfirm({
              maker: trimmedMaker,
              name: name.trim(),
              media_type: media,
              ...(displayName.trim() ? { display_name: displayName.trim() } : {}),
              facts,
              ...(saved ? { price: saved } : {}),
            });
            onClose();
          }}
        >
          {t("pm_manual_confirm")}
        </Button>
      </DialogFooter>
    </>
  );
}
