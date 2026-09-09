import { ChevronRight } from "lucide-react";
import { useId, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import type { UsageRecordDetail } from "@/types";
import { formatCurrencyAmount } from "@/utils/cost-format";
import { formatShortDateTime } from "@/utils/date-format";
import {
  MEDIA_META,
  STATUS_COLORS,
  STATUS_LABEL_KEYS,
  failurePhraseKey,
  formatDurationMs,
  providerLabelResolver,
  purposeKey,
} from "./usage-record-format";

interface UsageRecordDetailModalProps {
  recordId: number;
  detail: UsageRecordDetail | null;
  loading: boolean;
  failed: boolean;
  providerLabel: ReturnType<typeof providerLabelResolver>;
  onClose: () => void;
}

const GROUP_CLS =
  "font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-text-4";

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-t border-hairline-soft px-6 py-4 first:border-t-0">
      <h3 className={GROUP_CLS}>{title}</h3>
      <div className="mt-2">{children}</div>
    </section>
  );
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex gap-3 py-[3px] text-[11.5px]">
      <span className="w-20 shrink-0 text-text-4">{label}</span>
      <span className="min-w-0 flex-1 break-words font-mono text-[11px] text-text-2">
        {value}
      </span>
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
    <figure className="w-[92px]">
      {broken ? (
        <div className="grid h-[92px] w-[92px] place-items-center rounded-[8px] border border-hairline-soft text-[10px] text-text-4">
          {t("usage_image_missing")}
        </div>
      ) : (
        <img
          src={API.getFileUrl(projectName, path)}
          alt={caption ?? path}
          onError={() => setBroken(true)}
          className="h-[92px] w-[92px] rounded-[8px] border border-hairline-soft object-cover"
        />
      )}
      {caption && (
        <figcaption className="mt-1 truncate text-[10px] text-text-4">{caption}</figcaption>
      )}
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
    return <p className="text-[11.5px] text-text-4">{t("usage_detail_empty")}</p>;
  }

  return (
    <div className="space-y-3">
      {detail.prompt && (
        <p className="whitespace-pre-wrap rounded-[8px] border border-hairline-soft p-3 text-[11.5px] leading-[1.6] text-text-2">
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
            value={referenceAudio.join("\n")}
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
    return <p className="text-[11.5px] text-text-4">{t("usage_detail_empty")}</p>;
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
  const { t } = useTranslation(["dashboard", "common"]);
  const titleId = useId();
  const [rawOpen, setRawOpen] = useState(false);

  const media = detail ? MEDIA_META[detail.media_type] : null;
  const phraseKey = failurePhraseKey(detail?.error_code ?? null);
  const retryAfter = detail?.error_params?.retry_after_seconds;
  const failureStatus = detail?.error_params?.status;
  const purpose = purposeKey(detail?.purpose ?? null);
  const target = detail?.segment_id
    ? t("dashboard:usage_target_segment", { id: detail.segment_id })
    : purpose
      ? t(`dashboard:${purpose}`)
      : "—";

  return (
    <GlassModal
      open
      onClose={onClose}
      labelledBy={titleId}
      widthClassName="w-[36rem] max-w-[96vw]"
      panelClassName="max-h-[85vh] overflow-y-auto"
    >
      <header className="flex items-start gap-3 px-6 pb-3 pt-5">
        <div className="min-w-0 flex-1">
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
            Record · #{recordId}
          </div>
          <h2 id={titleId} className="mt-1 truncate text-[15px] font-medium text-text">
            {target}
          </h2>
          {detail && media && (
            <div className="mt-1.5 flex items-center gap-2 text-[11.5px] text-text-3">
              <media.Icon
                aria-hidden="true"
                className="h-3.5 w-3.5"
                style={{ color: media.color }}
              />
              <span>{t(`dashboard:${media.labelKey}`)}</span>
              <span aria-hidden="true">·</span>
              <span>{detail.project_name || t("dashboard:usage_project_untitled")}</span>
              <span aria-hidden="true">·</span>
              <span style={{ color: STATUS_COLORS[detail.status] }}>
                {t(`dashboard:${STATUS_LABEL_KEYS[detail.status]}`)}
              </span>
            </div>
          )}
        </div>
        <ModalCloseButton
          onClick={onClose}
          ariaLabel={t("dashboard:usage_detail_close")}
        />
      </header>

      {loading && (
        <p className="px-6 pb-6 text-[12px] text-text-3">{t("common:loading")}</p>
      )}
      {failed && (
        <p className="px-6 pb-6 text-[12px] text-danger-2">
          {t("dashboard:usage_load_failed")}
        </p>
      )}

      {detail && (
        <>
          {detail.status === "failed" && (
            <Group title={t("dashboard:usage_detail_group_failure")}>
              <div className="rounded-[8px] border border-danger-ring/60 bg-danger-soft p-3">
                <p className="text-[12px] text-danger-2">
                  {phraseKey
                    ? t(`dashboard:${phraseKey}`)
                    : (detail.error_message ?? t("dashboard:usage_detail_empty"))}
                </p>
                {phraseKey && detail.error_message && (
                  <p className="mt-1 break-words font-mono text-[11px] text-text-3">
                    {detail.error_message}
                  </p>
                )}
                {typeof retryAfter === "number" && (
                  <Field
                    label={t("dashboard:usage_field_retry_after")}
                    value={`${retryAfter} s`}
                  />
                )}
                {typeof failureStatus === "number" && (
                  <Field
                    label={t("dashboard:usage_field_http_status")}
                    value={failureStatus}
                  />
                )}
              </div>
            </Group>
          )}

          <Group title={t("dashboard:usage_detail_group_inputs")}>
            <InputsGroup detail={detail} />
          </Group>

          <Group title={t("dashboard:usage_detail_group_call")}>
            <div>
              <Field
                label={t("dashboard:usage_col_provider")}
                value={providerLabel(detail.provider)}
              />
              <Field label={t("dashboard:usage_col_model")} value={detail.model || "—"} />
              <Field
                label={t("dashboard:usage_field_purpose")}
                value={purpose ? t(`dashboard:${purpose}`) : "—"}
              />
              <Field
                label={t("dashboard:usage_field_task")}
                value={detail.task_type ?? "—"}
              />
              <Field
                label={t("dashboard:usage_field_started")}
                value={formatShortDateTime(detail.started_at) ?? "—"}
              />
              <Field
                label={t("dashboard:usage_field_finished")}
                value={formatShortDateTime(detail.finished_at) ?? "—"}
              />
              <Field
                label={t("dashboard:usage_col_duration")}
                value={formatDurationMs(detail.duration_ms, t)}
              />
            </div>
          </Group>

          <Group title={t("dashboard:usage_detail_group_output")}>
            {detail.output_path ? (
              <div className="flex items-start gap-3">
                {detail.media_type === "image" && (
                  <Thumbnail
                    projectName={detail.project_name}
                    path={detail.output_path}
                  />
                )}
                <Field
                  label={t("dashboard:usage_field_file")}
                  value={detail.output_path}
                />
              </div>
            ) : (
              <p className="text-[11.5px] text-text-4">
                {t("dashboard:usage_detail_empty")}
              </p>
            )}
          </Group>

          {detail.media_type === "text" && (
            <Group title={t("dashboard:usage_detail_group_usage")}>
              <UsageGroup detail={detail} />
            </Group>
          )}

          <Group title={t("dashboard:usage_col_cost")}>
            <p className="font-editorial text-[20px] leading-none text-text">
              {formatCurrencyAmount(detail.currency, detail.cost_amount, {
                maximumFractionDigits: 4,
              })}
              <span className="ml-1.5 font-mono text-[10px] text-text-4">
                {detail.currency}
              </span>
            </p>
            <p className="mt-2 text-[11px] text-text-4">
              {t("dashboard:usage_detail_cost_hint")}
            </p>
          </Group>

          <section className="border-t border-hairline-soft px-6 py-3">
            <button
              type="button"
              aria-expanded={rawOpen}
              onClick={() => setRawOpen((prev) => !prev)}
              className="focus-ring inline-flex items-center gap-1.5 text-[11.5px] text-text-3 transition-colors hover:text-text"
            >
              <ChevronRight
                aria-hidden="true"
                className={"h-3.5 w-3.5 transition-transform" + (rawOpen ? " rotate-90" : "")}
              />
              {t("dashboard:usage_detail_group_raw")}
            </button>
            {rawOpen && (
              <pre className="mt-2 max-h-[16rem] overflow-auto rounded-[8px] border border-hairline-soft p-3 font-mono text-[10.5px] leading-[1.5] text-text-3">
                {detail.last_provider_response
                  ? JSON.stringify(detail.last_provider_response, null, 2)
                  : t("dashboard:usage_detail_empty")}
              </pre>
            )}
          </section>
        </>
      )}
    </GlassModal>
  );
}
