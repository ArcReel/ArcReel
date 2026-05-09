/**
 * ImageModelDualSelect — 渐进式图片模型选择器（T2I + I2I）。
 *
 * 设计意图（spec: docs/superpowers/specs/2026-05-02-openai-image-endpoint-split-design.md）：
 * - 默认渲染单一下拉，列出所有 image backend 选项。
 * - 选中模型 caps ⊇ {T2I, I2I}（双能力 / 通配） → 仍 1 个下拉，valueT2I 与 valueI2I 同值写入。
 * - 选中模型 caps 仅 T2I → 当前下拉作为 T2I 槽；下方动态露出第 2 个 I2I 下拉，仅列 caps∋I2I 的模型。
 * - 选中模型 caps 仅 I2I → 镜像（先选 I2I 模型则露出 T2I 下拉）。
 *
 * Capability 来源：
 * - 内置 provider（gemini / ark / grok / openai 默认 mode "both"）：一律双能力。
 * - 自定义 provider：通过 customProviders[].models[].endpoint 反查 endpointToImageCapabilities。
 *   未传 customProviders 或 catalog 未就绪时，所有选项按双能力处理（等价于关闭过滤）。
 *
 * Label / hint 字符串通过 prop 注入而非内部 t()，使本组件可在 project 层
 * （命名空间 templates）与 system settings 层（命名空间 dashboard）共用。
 */

import { useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { ProviderModelSelect } from "@/components/ui/ProviderModelSelect";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type { CustomProviderInfo, ImageCap } from "@/types/custom-provider";

export interface ImageModelDualSelectProps {
  /** Current T2I value — empty string means "follow global default" */
  valueT2I: string;
  /** Current I2I value — empty string means "follow global default" */
  valueI2I: string;
  /** Available backend strings like "gemini/imagen-4" */
  options: string[];
  providerNames: Record<string, string>;
  /** 自定义供应商列表，用于派生 image capability；不传则所有选项按双能力处理。 */
  customProviders?: CustomProviderInfo[];
  /** Called when either slot changes */
  onChange: (next: { t2i: string; i2i: string }) => void;
  /** 单下拉模式 label（已 t()）；不传则使用 templates 命名空间默认值 model_image */
  labelPrimary?: string;
  /** 双下拉模式 T2I 槽 label（已 t()）；不传则使用 templates 默认 model_image_t2i */
  labelT2I?: string;
  /** 双下拉模式 I2I 槽 label（已 t()）；不传则使用 templates 默认 model_image_i2i */
  labelI2I?: string;
  /** 「跟随全局默认 / 自动选择」label（已 t()）；不传则使用 templates 默认值 */
  defaultLabel?: string;
  /** 「跟随全局默认」下方的提示文（已 t()）；与 globalDefault* 互斥 */
  defaultHint?: string;
  /** 显示「当前全局默认 = X」回退提示；仅 project 层有上级 default 可参考 */
  globalDefaultT2I?: string;
  globalDefaultI2I?: string;
  /** 是否显示底部 capability hint，默认 true（系统设置层可关掉） */
  showCapabilityHint?: boolean;
}

interface OptionCaps {
  t2i: boolean;
  i2i: boolean;
}

const BOTH_CAPS: OptionCaps = { t2i: true, i2i: true };

function buildCapsLookup(
  customProviders: CustomProviderInfo[],
  endpointToCaps: Record<string, ImageCap[]>,
): Record<string, OptionCaps> {
  const map: Record<string, OptionCaps> = {};
  for (const cp of customProviders) {
    const pid = `custom-${cp.id}`;
    for (const m of cp.models) {
      const caps = endpointToCaps[m.endpoint];
      if (!caps) continue;
      map[`${pid}/${m.model_id}`] = {
        t2i: caps.includes("text_to_image"),
        i2i: caps.includes("image_to_image"),
      };
    }
  }
  return map;
}

function capsOf(option: string, lookup: Record<string, OptionCaps>): OptionCaps {
  if (!option) return BOTH_CAPS;
  // 内置 provider 在 lookup 中不存在，按双能力处理（与 lib/image_backends 默认 mode 对齐）
  return lookup[option] ?? BOTH_CAPS;
}

export function ImageModelDualSelect({
  valueT2I,
  valueI2I,
  options,
  providerNames,
  customProviders,
  onChange,
  labelPrimary,
  labelT2I,
  labelI2I,
  defaultLabel,
  defaultHint,
  globalDefaultT2I,
  globalDefaultI2I,
  showCapabilityHint = true,
}: ImageModelDualSelectProps) {
  const { t } = useTranslation("templates");
  const fetchCatalog = useEndpointCatalogStore((s) => s.fetch);
  const endpointToCaps = useEndpointCatalogStore((s) => s.endpointToImageCapabilities);

  // 仅在出现自定义 provider 时才需要 catalog；否则保持惰性，避免无谓请求。
  const customProvidersLen = customProviders?.length ?? 0;
  useEffect(() => {
    if (customProvidersLen > 0) void fetchCatalog();
  }, [customProvidersLen, fetchCatalog]);

  const capsLookup = useMemo(
    () => buildCapsLookup(customProviders ?? [], endpointToCaps),
    [customProviders, endpointToCaps],
  );

  // 双下拉模式条件：
  // (1) 两槽值不一致（一空一非空 / 两不同模型）—— 用户已处于配置双槽状态；
  // (2) 两槽值相等且非空，但所选模型仅单能力 —— 异常初始状态（迁移残留或外部数据错配）
  //     需露出另一槽位让用户补全。catalog 异步就绪时本 useMemo 依赖 capsLookup，
  //     会自动重算 → 单能力会从 BOTH fallback 转为真实 caps，UI 自动展开为双下拉。
  const isDualMode = useMemo(() => {
    if (valueT2I !== valueI2I) return true;
    if (!valueT2I) return false;
    const caps = capsOf(valueT2I, capsLookup);
    return !caps.t2i || !caps.i2i;
  }, [valueT2I, valueI2I, capsLookup]);

  const t2iOptions = useMemo(
    () => options.filter((o) => capsOf(o, capsLookup).t2i),
    [options, capsLookup],
  );
  const i2iOptions = useMemo(
    () => options.filter((o) => capsOf(o, capsLookup).i2i),
    [options, capsLookup],
  );

  const fallbackLabel = defaultLabel ?? t("use_global_default");
  const t2iHint = globalDefaultT2I
    ? t("current_global_default", { value: globalDefaultT2I })
    : defaultHint;
  const i2iHint = globalDefaultI2I
    ? t("current_global_default", { value: globalDefaultI2I })
    : defaultHint;
  const dualHint = showCapabilityHint ? (
    <p className="text-[11.5px] leading-[1.5] text-text-4">{t("model_image_dual_hint")}</p>
  ) : null;

  // 单下拉模式 onChange：依据所选模型 caps 自动决定写入哪个槽。
  const handlePrimaryChange = (next: string) => {
    if (!next) {
      onChange({ t2i: "", i2i: "" });
      return;
    }
    const caps = capsOf(next, capsLookup);
    if (caps.t2i && caps.i2i) {
      onChange({ t2i: next, i2i: next });
    } else if (caps.t2i) {
      onChange({ t2i: next, i2i: "" });
    } else if (caps.i2i) {
      onChange({ t2i: "", i2i: next });
    } else {
      // 既无 T2I 也无 I2I（理论上不该出现在 image_backends 列表）→ 全清
      onChange({ t2i: "", i2i: "" });
    }
  };

  if (!isDualMode) {
    return (
      <div className="space-y-3">
        <ProviderModelSelect
          value={valueT2I}
          options={options}
          providerNames={providerNames}
          onChange={handlePrimaryChange}
          allowDefault
          defaultLabel={fallbackLabel}
          defaultHint={t2iHint}
          fallbackValue={globalDefaultT2I || undefined}
          aria-label={labelPrimary ?? t("model_image")}
        />
        {dualHint}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4">{labelT2I ?? t("model_image_t2i")}</div>
        <ProviderModelSelect
          value={valueT2I}
          options={t2iOptions}
          providerNames={providerNames}
          onChange={(next) => onChange({ t2i: next, i2i: valueI2I })}
          allowDefault
          defaultLabel={fallbackLabel}
          defaultHint={t2iHint}
          fallbackValue={globalDefaultT2I || undefined}
          aria-label={labelT2I ?? t("model_image_t2i")}
        />
      </div>

      <div>
        <div className="mb-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4">{labelI2I ?? t("model_image_i2i")}</div>
        <ProviderModelSelect
          value={valueI2I}
          options={i2iOptions}
          providerNames={providerNames}
          onChange={(next) => onChange({ t2i: valueT2I, i2i: next })}
          allowDefault
          defaultLabel={fallbackLabel}
          defaultHint={i2iHint}
          fallbackValue={globalDefaultI2I || undefined}
          aria-label={labelI2I ?? t("model_image_i2i")}
        />
      </div>

      {dualHint}
    </div>
  );
}
