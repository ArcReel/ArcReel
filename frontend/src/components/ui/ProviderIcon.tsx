import BailianColor from "@lobehub/icons/es/Bailian/components/Color";
import GeminiColor from "@lobehub/icons/es/Gemini/components/Color";
import GrokMono from "@lobehub/icons/es/Grok/components/Mono";
import KlingColor from "@lobehub/icons/es/Kling/components/Color";
import MinimaxColor from "@lobehub/icons/es/Minimax/components/Color";
import OpenAIMono from "@lobehub/icons/es/OpenAI/components/Mono";
import VertexAIColor from "@lobehub/icons/es/VertexAI/components/Color";
import ViduColor from "@lobehub/icons/es/Vidu/components/Color";
import VolcengineColor from "@lobehub/icons/es/Volcengine/components/Color";
import type { ComponentType } from "react";

export const PROVIDER_NAMES: Record<string, string> = {
  "gemini-aistudio": "AI Studio",
  "gemini-vertex": "Vertex AI",
  ark: "火山方舟",
  grok: "Grok",
  openai: "OpenAI",
  vidu: "Vidu",
};

/**
 * 内置 provider 的 canonical id → lobehub 具名图标组件。逐个静态 import 保持 tree-shaking。
 * 键为规范化后的小写 id（仅 a-z0-9）；id 与 lobehub 图标名不一致的（如 ark/dashscope）
 * 由 resolveIconKey 折叠到这里的规范键。图标库本就没有的内置 provider（如 Agnes）不登记，
 * 自然回落字母徽章——徽章是合法终态而非缺漏，不必为「覆盖全部 provider」造假图标。
 */
const ICON_REGISTRY: Record<string, ComponentType<{ className?: string }>> = {
  gemini: GeminiColor,
  vertexai: VertexAIColor,
  grok: GrokMono,
  volcengine: VolcengineColor,
  bailian: BailianColor,
  minimax: MinimaxColor,
  openai: OpenAIMono,
  vidu: ViduColor,
  kling: KlingColor,
};

/**
 * 内置 provider 的 canonical id → ICON_REGISTRY 的规范键。
 * id 的前缀/别名家族（gemini-vertex、gemini-*、grok-*、ark-*、dashscope）在此折叠。
 * 仅吃 canonical id：自定义 provider 不走这里（恒用字母徽章、不按名字猜品牌），
 * 故前缀规则不会误配自由文本名（如 Arknights 不会被 ark-* 命中火山方舟）。
 */
function resolveIconKey(providerId: string): string {
  const id = providerId.toLowerCase();
  if (id === "gemini-vertex") return "vertexai";
  if (id.startsWith("gemini")) return "gemini";
  if (id.startsWith("grok")) return "grok";
  if (id.startsWith("ark")) return "volcengine";
  if (id === "dashscope") return "bailian";
  return id.replace(/[^a-z0-9]/g, "");
}

/**
 * 按内置 provider 的 canonical id 渲染品牌图标，命中 ICON_REGISTRY 则出图标，
 * 否则回落字母徽章（图标库没有该供应商时的合法终态）。
 */
export function ProviderIcon({ providerId, className }: { providerId: string; className?: string }) {
  const cls = className ?? "h-6 w-6";
  const Icon = ICON_REGISTRY[resolveIconKey(providerId)];
  if (Icon) return <Icon className={cls} />;
  // Fallback: 字母徽章。providerId 类型即 string（resolveIconKey 已直接 .toLowerCase()），
  // 这里同样信任契约不再 ?? ""；Array.from 取首字符避免星平面字符被截成半个代理对。
  return (
    <span className={`inline-flex items-center justify-center rounded border border-hairline-soft bg-bg-grad-b/70 text-xs font-bold uppercase text-text-2 ${cls}`}>
      {Array.from(providerId)[0] ?? "?"}
    </span>
  );
}
