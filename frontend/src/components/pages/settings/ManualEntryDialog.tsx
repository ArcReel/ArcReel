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
import { EDITABLE_FACTS, FactField } from "./ProviderModelEntryInfo";
import { PriceEditor } from "./price-editor/PriceEditor";
import { isPriceTableValid, priceForSave, priceTableOf, type PriceTable } from "./price-editor/priceTable";
import { MEDIA_LABEL_KEY } from "./providerModelDisplay";

type MediaType = ProviderModelItem["media_type"];

const MEDIA_TYPES: readonly MediaType[] = ["text", "image", "video", "audio"];

/** 手动填写时列出的模型事实：与「模型信息」可修改的一致，时长联动约束留到保存之后再设。 */
const FILL_IN_FACTS = (media: MediaType): CapabilityKey[] => EDITABLE_FACTS[media].filter((key) => key !== "duration_constraints");

/** 各媒体类型的初始取值。文本的输出上限是常见值；视频的可选时长随名字预填，见 `ManualEntryDialog`。 */
function defaultFacts(media: MediaType): Record<string, unknown> {
  switch (media) {
    case "text":
      return { image_input: false, structured_output: false, max_output_tokens: 8192 };
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
  /** 给了就固定媒体类型（供应商模型的调用端点已决定类型）；不给则让用户选择。 */
  mediaType?: MediaType;
  /** 模型名称的初值，一般是正在设置的模型 ID。 */
  initialName?: string;
  /** 再次编辑一个还没保存的新条目。 */
  initial?: NewCatalogEntry | null;
  /** 填好了：调用方把它放进自己的草稿，随自己的保存一起写入；对话框不保存任何东西。 */
  onConfirm: (entry: NewCatalogEntry) => void;
  /** 目录里已有同名模型时，让用户直接选择它。 */
  onUseExisting?: (hit: CatalogEntryHit) => void;
}

/**
 * 「手动填写模型信息」：为目录里找不到的模型填写厂商、名称、类型与能力，价格可以不填。只产出一个新条目的内容，
 * 不写入任何东西：Sheet 把它放进草稿，随保存写入，取消则不留下条目；添加模型的对话框可以直接调用
 * `API.createCatalogEntry` 写入。
 */
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
  const [media, setMedia] = useState<MediaType>(initial?.media_type ?? mediaType ?? "text");
  const [facts, setFacts] = useState<Record<string, unknown>>(initial?.facts ?? defaultFacts(media));
  const [price, setPrice] = useState<PriceTable>(() => priceTableOf(initial?.price));
  // 用户自己改过可选时长之后，不再随名字预填
  const [durationsTouched, setDurationsTouched] = useState(initial !== undefined && initial !== null);
  const [invalidKeys, setInvalidKeys] = useState<ReadonlySet<string>>(new Set());
  // 提示对应的是哪个名字与媒体类型：名字或类型变了以后，旧的提示不再适用
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

  // 目录里是否已有同名模型，以及按名字预设预填的可选时长
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
    setInvalidKeys(new Set());
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
                {MEDIA_TYPES.map((type) => (
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
