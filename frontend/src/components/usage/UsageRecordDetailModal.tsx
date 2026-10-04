import { ChevronRight } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { UsageRecordDetail } from "@/types";
import { formatCurrencyAmount } from "@/utils/cost-format";
import { formatShortDateTime } from "@/utils/date-format";
import { itemIdsInEpisodeText } from "@/utils/episode-display";
import {
  MEDIA_META,
  STATUS_LABEL_KEYS,
  STATUS_TEXT_CLASSES,
  failurePhraseKey,
  formatDurationMs,
  providerLabelResolver,
  purposeKey,
  targetParts,
  usageProjectLabel,
} from "./usage-record-format";

interface UsageRecordDetailModalProps {
  recordId: number;
  detail: UsageRecordDetail | null;
  loading: boolean;
  failed: boolean;
  providerLabel: ReturnType<typeof providerLabelResolver>;
  onClose: () => void;
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2 border-t border-border pt-4 first:border-t-0 first:pt-0">
      <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex gap-3 py-0.5 text-sm">
      <span className="w-24 shrink-0 text-muted-foreground">{label}</span>
      <span className="num min-w-0 flex-1 text-xs break-words whitespace-pre-wrap text-subtle-foreground">{value}</span>
    </div>
  );
}

/** 项目内相对路径的缩略图；文件已不存在时换成占位，历史记录也能照常打开。 */
function Thumbnail({
  projectName,
  path,
  caption,
}: {
  projectName: string;
  path: string;
  caption?: string | null;
}) {
  const { t } = useTranslation("dashboard");
  const [broken, setBroken] = useState(false);
  return (
    <figure className="flex w-24 flex-col gap-1">
      {broken ? (
        <div className="grid size-24 place-items-center rounded-md border border-border text-xs text-muted-foreground">
          {t("usage_image_missing")}
        </div>
      ) : (
        <img
          src={API.getFileUrl(projectName, path)}
          alt={itemIdsInEpisodeText(caption ?? path)}
          onError={() => setBroken(true)}
          className="size-24 rounded-md border border-border object-cover"
        />
      )}
      {caption && <figcaption className="truncate text-xs text-muted-foreground">{itemIdsInEpisodeText(caption)}</figcaption>}
    </figure>
  );
}

function InputsGroup({ detail }: { detail: UsageRecordDetail }) {
  const { t } = useTranslation("dashboard");
  const inputs = detail.inputs;
  const images = inputs?.reference_images ?? [];
  const referenceAudio = inputs?.reference_audio ?? [];
  const params = inputs?.parameters;
  const hasAny =
    Boolean(detail.prompt) ||
    images.length > 0 ||
    Boolean(inputs?.start_image) ||
    Boolean(inputs?.end_image) ||
    referenceAudio.length > 0 ||
    Boolean(inputs?.voice) ||
    Boolean(params && Object.keys(params).length > 0) ||
    detail.resolution !== null ||
    detail.aspect_ratio !== null ||
    detail.duration_seconds !== null;

  if (!hasAny) {
    return <p className="text-sm text-muted-foreground">{t("usage_detail_empty")}</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      {detail.prompt && (
        <p className="max-w-[40em] rounded-md border border-border p-3 text-sm leading-relaxed whitespace-pre-wrap text-subtle-foreground">
          {detail.prompt}
        </p>
      )}
      {(images.length > 0 || inputs?.start_image || inputs?.end_image) && (
        <div className="flex flex-wrap gap-3">
          {images.map((image) => (
            <Thumbnail
              key={image.path}
              projectName={detail.project_name}
              path={image.path}
              caption={image.label ?? image.role ?? null}
            />
          ))}
          {inputs?.start_image && (
            <Thumbnail
              projectName={detail.project_name}
              path={inputs.start_image}
              caption={t("usage_field_first_frame")}
            />
          )}
          {inputs?.end_image && (
            <Thumbnail
              projectName={detail.project_name}
              path={inputs.end_image}
              caption={t("usage_field_last_frame")}
            />
          )}
        </div>
      )}
      <div>
        {inputs?.voice && <Field label={t("usage_field_voice")} value={inputs.voice} />}
        {referenceAudio.length > 0 && (
          <Field
            label={t("usage_field_reference_audio")}
            value={referenceAudio.map(itemIdsInEpisodeText).join("\n")}
          />
        )}
        {detail.resolution && (
          <Field label={t("usage_field_resolution")} value={detail.resolution} />
        )}
        {detail.aspect_ratio && (
          <Field label={t("usage_field_aspect_ratio")} value={detail.aspect_ratio} />
        )}
        {detail.duration_seconds !== null && (
          <Field
            label={t("usage_field_duration_seconds")}
            value={`${detail.duration_seconds} s`}
          />
        )}
        {params && Object.keys(params).length > 0 && (
          <Field
            label={t("usage_field_params")}
            value={JSON.stringify(params, null, 2)}
          />
        )}
      </div>
    </div>
  );
}

function UsageGroup({ detail }: { detail: UsageRecordDetail }) {
  const { t } = useTranslation("dashboard");
  const rows: [string, number | null][] = [
    [t("usage_field_input_tokens"), detail.input_tokens],
    [t("usage_field_output_tokens"), detail.output_tokens],
    [t("usage_field_total_tokens"), detail.usage_tokens],
  ].filter(([, value]) => value !== null) as [string, number][];
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("usage_detail_empty")}</p>;
  }
  return (
    <div>
      {rows.map(([label, value]) => (
        <Field key={label} label={label} value={(value as number).toLocaleString()} />
      ))}
    </div>
  );
}

export function UsageRecordDetailModal({
  recordId,
  detail,
  loading,
  failed,
  providerLabel,
  onClose,
}: UsageRecordDetailModalProps) {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  const [rawOpen, setRawOpen] = useState(false);

  const media = detail ? MEDIA_META[detail.media_type] : null;
  const phraseKey = failurePhraseKey(detail?.error_code ?? null);
  const retryAfter = detail?.error_params?.retry_after_seconds;
  const failureStatus = detail?.error_params?.status;
  const purpose = purposeKey(detail?.purpose ?? null);
  const target = detail ? targetParts(detail.segment_id, detail.segment_ref ?? null, detail.purpose, t) : null;

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>
            {target ? (
              <span className="flex min-w-0 items-baseline gap-2">
                {target.prefix && <span className="num shrink-0">{target.prefix}</span>}
                <span className="min-w-0 break-words">{target.name}</span>
              </span>
            ) : (
              t("dashboard:usage_detail_title", { id: recordId })
            )}
          </DialogTitle>
          {detail && media ? (
            <DialogDescription>
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="inline-flex items-center gap-1.5">
                  <media.Icon aria-hidden="true" className={cn("size-3.5", media.iconClass)} />
                  {t(`dashboard:${media.labelKey}`)}
                </span>
                <span aria-hidden="true">·</span>
                <span>{usageProjectLabel(detail.project_name, t, i18n.language, detail.project_title)}</span>
                <span aria-hidden="true">·</span>
                <span className={STATUS_TEXT_CLASSES[detail.status]}>
                  {t(`dashboard:${STATUS_LABEL_KEYS[detail.status]}`)}
                </span>
              </span>
            </DialogDescription>
          ) : (
            <DialogDescription className="sr-only">{t("dashboard:usage_detail_title", { id: recordId })}</DialogDescription>
          )}
        </DialogHeader>

        <DialogBody>
          <div className="flex flex-col gap-4">
            {loading && <p className="text-sm text-muted-foreground">{t("common:loading")}</p>}
            {failed && <p className="text-sm text-destructive">{t("dashboard:usage_load_failed")}</p>}

            {detail && (
              <>
                {detail.status === "failed" && (
                  <Group title={t("dashboard:usage_detail_group_failure")}>
                    <div className="flex flex-col gap-1 rounded-md border border-destructive/20 bg-destructive/10 p-3">
                      <p className="text-sm text-destructive">
                        {phraseKey
                          ? t(`dashboard:${phraseKey}`)
                          : (detail.error_message ?? t("dashboard:usage_detail_empty"))}
                      </p>
                      {phraseKey && detail.error_message && (
                        <p className="num text-xs break-words text-muted-foreground">{detail.error_message}</p>
                      )}
                      {typeof retryAfter === "number" && (
                        <Field label={t("dashboard:usage_field_retry_after")} value={`${retryAfter} s`} />
                      )}
                      {typeof failureStatus === "number" && (
                        <Field label={t("dashboard:usage_field_http_status")} value={failureStatus} />
                      )}
                    </div>
                  </Group>
                )}

                <Group title={t("dashboard:usage_detail_group_inputs")}>
                  <InputsGroup detail={detail} />
                </Group>

                <Group title={t("dashboard:usage_detail_group_call")}>
                  <div>
                    <Field label={t("dashboard:usage_col_provider")} value={providerLabel(detail.provider)} />
                    <Field label={t("dashboard:usage_col_model")} value={detail.model || "—"} />
                    <Field
                      label={t("dashboard:usage_field_purpose")}
                      value={purpose ? t(`dashboard:${purpose}`) : "—"}
                    />
                    <Field label={t("dashboard:usage_field_task")} value={detail.task_type ?? "—"} />
                    <Field
                      label={t("dashboard:usage_field_started")}
                      value={formatShortDateTime(detail.started_at) ?? "—"}
                    />
                    <Field
                      label={t("dashboard:usage_field_finished")}
                      value={formatShortDateTime(detail.finished_at) ?? "—"}
                    />
                    <Field label={t("dashboard:usage_col_duration")} value={formatDurationMs(detail.duration_ms, t)} />
                  </div>
                </Group>

                <Group title={t("dashboard:usage_detail_group_output")}>
                  {detail.output_path ? (
                    <div className="flex items-start gap-3">
                      {detail.media_type === "image" && (
                        <Thumbnail projectName={detail.project_name} path={detail.output_path} />
                      )}
                      <Field label={t("dashboard:usage_field_file")} value={itemIdsInEpisodeText(detail.output_path)} />
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">{t("dashboard:usage_detail_empty")}</p>
                  )}
                </Group>

                {detail.media_type === "text" && (
                  <Group title={t("dashboard:usage_detail_group_usage")}>
                    <UsageGroup detail={detail} />
                  </Group>
                )}

                <Group title={t("dashboard:usage_col_cost")}>
                  <p className="num text-xl text-foreground">
                    {formatCurrencyAmount(detail.currency, detail.cost_amount, { maximumFractionDigits: 4 })}
                    <span className="ml-1.5 text-xs text-muted-foreground">{detail.currency}</span>
                  </p>
                  <p className="text-xs text-muted-foreground">{t("dashboard:usage_detail_cost_hint")}</p>
                </Group>

                <section className="flex flex-col gap-2 border-t border-border pt-3">
                  <Button
                    size="xs"
                    variant="ghost"
                    className="self-start"
                    aria-expanded={rawOpen}
                    onClick={() => setRawOpen((prev) => !prev)}
                  >
                    <ChevronRight
                      aria-hidden="true"
                      data-icon="inline-start"
                      className={cn("transition-transform", rawOpen && "rotate-90")}
                    />
                    {t("dashboard:usage_detail_group_raw")}
                  </Button>
                  {rawOpen && (
                    <pre className="relative max-h-64 overflow-auto rounded-md border border-border p-3 text-xs leading-normal text-muted-foreground">
                      {detail.last_provider_response
                        ? JSON.stringify(detail.last_provider_response, null, 2)
                        : t("dashboard:usage_detail_empty")}
                    </pre>
                  )}
                </section>
              </>
            )}
          </div>
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
