/**
 * LayeredModelFields —— 「默认模型 + 按用途指定模型」的同源交互形态（docs/adr/0054）。
 *
 * 文本档位、图片能力桶、视频能力桶三处共用：一个常驻的默认主下拉，下方一个默认收起的
 * 折叠区收纳细分项。全局设置、项目设置两层复用同一组件，创建向导不传 subFields 即只剩默认层。
 *
 * 细分项留空时触发按钮显示穿透演算后的最终生效模型，用户不展开也能看到会真正执行的模型；
 * 演算结果由调用方按各层键位算好传入，本组件不持有层级知识。
 *
 * 界面文案统一用「按用途指定模型」，不出现「能力」「桶」字样（见 CONTEXT.md 能力桶词条）。
 */

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight } from "lucide-react";
import { ProviderModelSelect } from "@/components/ui/ProviderModelSelect";
import type { CapabilityBucket } from "@/types/system";

/**
 * 层级解析（docs/adr/0054）：取第一个非空层作为生效值。全层皆空即自动推断——那需要
 * 供应商就绪状态与注册表顺序，前端算不出，返回 undefined 由调用处显示「自动选择」。
 */
export function effectiveModel(...layers: (string | null | undefined)[]): string | undefined {
  return layers.find((layer) => !!layer) || undefined;
}

/**
 * 当前配置下真正会执行的视频模型。参考生视频走 r2v 桶、其余生成模式走 i2v，与后端
 * `VIDEO_BUCKET_BY_GENERATION_MODE` 同口径；桶被覆盖时它与默认层不是同一个模型，
 * 故时长 / 分辨率 / 声音档位这些按模型查的能力，以及 model_settings 的分辨率键，都问它。
 */
export function executingVideoModel(
  value: { videoBackend: string; videoProviderI2V: string; videoProviderR2V: string },
  globals: { video: string; videoI2V: string; videoR2V: string },
  usesReferenceImages?: boolean,
): string {
  const bucket = usesReferenceImages ? value.videoProviderR2V : value.videoProviderI2V;
  const globalBucket = usesReferenceImages ? globals.videoR2V : globals.videoI2V;
  return effectiveModel(bucket, value.videoBackend, globalBucket, globals.video) ?? "";
}

/**
 * 文生图路径真正会执行的图片模型——分镜图与资产图都走该路径，图片分辨率按它存取。
 * 图生图另有自己的执行模型，两者不同时后端查不到该键、回落供应商默认档位。
 */
export function executingImageModel(
  value: { imageBackendDefault: string; imageBackendT2I: string },
  globals: { image: string; imageT2I: string },
): string {
  return effectiveModel(value.imageBackendT2I, value.imageBackendDefault, globals.imageT2I, globals.image) ?? "";
}

/** 能力桶的界面标签与覆盖说明，图片 / 视频两处调用点共用一份文案。 */
export function useCapabilityBucketLabels(): Record<CapabilityBucket, { label: string; caption: string }> {
  const { t } = useTranslation("templates");
  return useMemo(
    () => ({
      t2i: { label: t("bucket_t2i_label"), caption: t("bucket_t2i_caption") },
      i2i: { label: t("bucket_i2i_label"), caption: t("bucket_i2i_caption") },
      i2v: { label: t("bucket_i2v_label"), caption: t("bucket_i2v_caption") },
      r2v: { label: t("bucket_r2v_label"), caption: t("bucket_r2v_caption") },
    }),
    [t],
  );
}

export interface LayeredSubField {
  key: string;
  /** 已 t() 的细分项标签，以生成路径命名（文生图 / 图生图 / 图生视频 / 参考生视频）。 */
  label: string;
  /** 已 t() 的覆盖范围说明，常驻在下拉下方。 */
  caption: string;
  value: string;
  /** 该细分项的候选（细分层按能力过滤）。 */
  options: string[];
  /** 留空时的最终生效模型（provider/model）；演算不出即省略。 */
  effective?: string;
  onChange: (next: string) => void;
}

/**
 * 候选拉取失败时的细分区降级：只保留已配置的行、候选只列其当前值。已保存的覆盖在后端仍然
 * 生效，整块隐藏会让用户既看不出实际执行的是哪个模型，也无从清除；未配置的行没有候选可选，
 * 仍不渲染，全部未配置即整块折叠区消失。
 */
export function degradeSubFieldsToSaved(
  fields: LayeredSubField[],
  hasCandidates: boolean,
): LayeredSubField[] {
  if (hasCandidates) return fields;
  return fields.filter((f) => !!f.value).map((f) => ({ ...f, options: [f.value] }));
}

export interface LayeredModelFieldsProps {
  /** 默认层下拉的无障碍标签；视觉标签由外层卡片标题承担。 */
  defaultLabel: string;
  defaultValue: string;
  /** 默认层候选，不过滤——默认层不承诺能力，能力不满足由解析闸报错兜底。 */
  defaultOptions: string[];
  onDefaultChange: (next: string) => void;
  /** 默认层空值选项的标签：全局设置为「自动选择」，项目设置为「使用全局默认」。 */
  emptyLabel: string;
  emptyHint?: string;
  /** 默认层留空时的生效模型（项目层 = 全局默认层）；全局层是基准、不传。 */
  defaultEffective?: string;
  providerNames: Record<string, string>;
  modelNames?: Record<string, string>;
  renderOptionMeta?: (fullValue: string) => React.ReactNode;
  /** 默认层下拉与折叠区之间的附加内容（模型规格条、分辨率、时长等）。 */
  children?: React.ReactNode;
  /** 细分项；省略或空数组即不渲染折叠区（创建向导只暴露默认层）。 */
  subFields?: LayeredSubField[];
  /** 折叠区之后常驻的补充说明（如文本档位的智能体边界）。 */
  footnote?: React.ReactNode;
}

const SUB_LABEL_CLS = "mb-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4";

export function LayeredModelFields({
  defaultLabel,
  defaultValue,
  defaultOptions,
  onDefaultChange,
  emptyLabel,
  emptyHint,
  defaultEffective,
  providerNames,
  modelNames,
  renderOptionMeta,
  children,
  subFields,
  footnote,
}: LayeredModelFieldsProps) {
  const { t } = useTranslation("templates");
  const fields = subFields ?? [];
  const configuredCount = fields.filter((f) => !!f.value).length;

  // 默认收起；但已指定过细分项时初始展开——收起会让已生效的覆盖完全不可见，
  // 用户改默认模型时会误以为改动即刻生效。挂载后由用户自行开合。
  const [open, setOpen] = useState(() => fields.some((f) => !!f.value));

  return (
    <div className="space-y-3">
      <ProviderModelSelect
        value={defaultValue}
        options={defaultOptions}
        providerNames={providerNames}
        modelNames={modelNames}
        onChange={onDefaultChange}
        allowDefault
        defaultLabel={emptyLabel}
        defaultHint={emptyHint}
        // 全层皆空时演算不出具体模型，触发按钮落到 placeholder。这里给的是空值语义本身
        // （「自动选择」/「使用全局默认」），否则通用的「选择模型」会读成这项必须选。
        placeholder={emptyLabel}
        fallbackValue={defaultEffective}
        aria-label={defaultLabel}
        renderOptionMeta={renderOptionMeta}
      />

      {children}

      {fields.length > 0 && (
        <details
          className="group border-t border-hairline-soft pt-3"
          open={open}
          onToggle={(e) => setOpen(e.currentTarget.open)}
        >
          <summary className="flex cursor-pointer list-none items-center gap-2 rounded-[7px] font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4 transition-colors hover:text-text-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent [&::-webkit-details-marker]:hidden">
            <ChevronRight
              aria-hidden
              className="h-3.5 w-3.5 shrink-0 motion-safe:transition-transform group-open:rotate-90"
            />
            <span>{t("model_bucket_section")}</span>
            {configuredCount > 0 && (
              <span className="shrink-0 rounded-full border border-accent/45 bg-accent-dim px-2 py-0.5 text-[9.5px] tracking-[0.1em] text-accent-2">
                {t("model_bucket_configured_count", { n: configuredCount })}
              </span>
            )}
          </summary>

          <div className="mt-3 space-y-3.5 border-l border-hairline-soft pl-3">
            <p className="text-[11px] leading-[1.5] text-text-4">{t("model_bucket_section_hint")}</p>
            {fields.map((field) => (
              <div key={field.key}>
                <div className={SUB_LABEL_CLS}>{field.label}</div>
                <ProviderModelSelect
                  value={field.value}
                  options={field.options}
                  providerNames={providerNames}
                  modelNames={modelNames}
                  onChange={field.onChange}
                  allowDefault
                  defaultLabel={t("follow_model_default")}
                  placeholder={t("follow_model_default")}
                  fallbackValue={field.effective}
                  fallbackLabel={t("follow_model_default")}
                  aria-label={field.label}
                  renderOptionMeta={renderOptionMeta}
                />
                <p className="mt-1.5 text-[11px] leading-[1.5] text-text-4">{field.caption}</p>
              </div>
            ))}
          </div>
        </details>
      )}

      {footnote}
    </div>
  );
}
