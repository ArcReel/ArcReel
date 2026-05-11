import { type ComponentType, useEffect, useState } from "react";

interface IconProps {
  size?: number;
}

type IconLoader = () => Promise<{ default: ComponentType<IconProps> }>;

/**
 * iconKey → @lobehub/icons 子组件路径。
 *
 * 与 lib/agent_provider_catalog.py 的 PresetProvider.icon_key 一一对应。
 * 优先使用 Color 变体；当上游包没有提供 Color 时回退 Mono。
 * 新增供应商时如缺失映射，组件会 fallback 到 monogram。
 */
const ICON_LOADERS: Record<string, IconLoader> = {
  Anthropic: () => import("@lobehub/icons/es/Anthropic/components/Mono"),
  Aws: () => import("@lobehub/icons/es/Aws/components/Color"),
  Bedrock: () => import("@lobehub/icons/es/Bedrock/components/Color"),
  ChatGLM: () => import("@lobehub/icons/es/ChatGLM/components/Color"),
  Claude: () => import("@lobehub/icons/es/Claude/components/Color"),
  ClaudeCode: () => import("@lobehub/icons/es/ClaudeCode/components/Color"),
  DeepSeek: () => import("@lobehub/icons/es/DeepSeek/components/Color"),
  Doubao: () => import("@lobehub/icons/es/Doubao/components/Color"),
  Gemini: () => import("@lobehub/icons/es/Gemini/components/Color"),
  Google: () => import("@lobehub/icons/es/Google/components/Color"),
  Hunyuan: () => import("@lobehub/icons/es/Hunyuan/components/Color"),
  Kimi: () => import("@lobehub/icons/es/Kimi/components/Color"),
  KwaiKAT: () => import("@lobehub/icons/es/KwaiKAT/components/Mono"),
  LongCat: () => import("@lobehub/icons/es/LongCat/components/Color"),
  Minimax: () => import("@lobehub/icons/es/Minimax/components/Color"),
  Moonshot: () => import("@lobehub/icons/es/Moonshot/components/Mono"),
  Nvidia: () => import("@lobehub/icons/es/Nvidia/components/Color"),
  OpenAI: () => import("@lobehub/icons/es/OpenAI/components/Mono"),
  OpenRouter: () => import("@lobehub/icons/es/OpenRouter/components/Mono"),
  Qwen: () => import("@lobehub/icons/es/Qwen/components/Color"),
  SiliconCloud: () => import("@lobehub/icons/es/SiliconCloud/components/Color"),
  Stepfun: () => import("@lobehub/icons/es/Stepfun/components/Color"),
  Tencent: () => import("@lobehub/icons/es/Tencent/components/Color"),
  TencentCloud: () => import("@lobehub/icons/es/TencentCloud/components/Color"),
  Volcengine: () => import("@lobehub/icons/es/Volcengine/components/Color"),
  XiaomiMiMo: () => import("@lobehub/icons/es/XiaomiMiMo/components/Mono"),
  Zhipu: () => import("@lobehub/icons/es/Zhipu/components/Color"),
};

interface Props {
  iconKey: string | null;
  size?: number;
  className?: string;
}

interface LoadedIcon {
  key: string;
  component: ComponentType<IconProps>;
}

export function PresetIcon({ iconKey, size = 20, className }: Props) {
  const [loaded, setLoaded] = useState<LoadedIcon | null>(null);

  useEffect(() => {
    if (!iconKey) return;
    const loader = ICON_LOADERS[iconKey];
    if (!loader) return;
    let cancelled = false;
    void loader()
      .then((m) => {
        if (!cancelled) setLoaded({ key: iconKey, component: m.default });
      })
      .catch(() => {
        /* fall through to monogram */
      });
    return () => {
      cancelled = true;
    };
  }, [iconKey]);

  const Icon = loaded && loaded.key === iconKey ? loaded.component : null;
  if (Icon)
    return (
      <span className={className}>
        <Icon size={size} />
      </span>
    );
  // Monogram fallback
  const letter = (iconKey?.[0] ?? "?").toUpperCase();
  return (
    <span
      data-testid="preset-icon-monogram"
      className={`inline-flex items-center justify-center rounded-md bg-bg-grad-a text-[11px] font-bold text-text-3 ${className ?? ""}`}
      style={{ width: size, height: size }}
    >
      {letter}
    </span>
  );
}
