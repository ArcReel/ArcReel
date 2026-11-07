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

/**
 * Built-in provider canonical IDs → named lobehub icon components. Individual static imports preserve tree shaking.
 * Keys are normalized lowercase IDs (a-z0-9 only); differing IDs/lobehub names (e.g. ark/dashscope)
 * map through resolveIconKey to canonical keys here. Omit providers absent from the icon library (e.g. Agnes),
 * naturally falling back to letter badges, valid final states rather than omissions; never invent icons to cover all providers.
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
 * Built-in provider canonical IDs → ICON_REGISTRY canonical keys.
 * Collapse prefix/alias families (gemini-vertex, gemini-*, grok-*, ark-*, dashscope) here.
 * Accept canonical IDs only; custom providers bypass this (always letter badges, never guessing brands from names),
 * so prefix rules cannot misidentify free-text names (e.g. Arknights never matches ark-* as Volcengine Ark).
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
 * Render built-in provider brand icons by canonical IDs when ICON_REGISTRY matches;
 * otherwise use letter badges, valid final states for providers absent from icon libraries.
 */
export function ProviderIcon({ providerId, className }: { providerId: string; className?: string }) {
  const cls = className ?? "h-6 w-6";
  const Icon = ICON_REGISTRY[resolveIconKey(providerId)];
  if (Icon) return <Icon className={cls} />;
  // Fallback: letter badges. providerId is already string (resolveIconKey directly calls .toLowerCase());
  // trust contracts here too without ?? ""; Array.from takes first characters without splitting astral-plane surrogate pairs.
  return (
    <span className={`inline-flex items-center justify-center rounded-sm border border-border/50 bg-sidebar/70 text-xs font-bold uppercase text-subtle-foreground ${cls}`}>
      {Array.from(providerId)[0] ?? "?"}
    </span>
  );
}
