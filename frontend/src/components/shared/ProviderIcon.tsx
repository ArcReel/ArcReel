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

function resolveIconKey(providerId: string): string {
  const id = providerId.toLowerCase();
  if (id === "gemini-vertex") return "vertexai";
  if (id.startsWith("gemini")) return "gemini";
  if (id.startsWith("grok")) return "grok";
  if (id.startsWith("ark")) return "volcengine";
  if (id === "dashscope") return "bailian";
  return id.replace(/[^a-z0-9]/g, "");
}

export function ProviderIcon({ providerId, className }: { providerId: string; className?: string }) {
  const cls = className ?? "h-6 w-6";
  const Icon = ICON_REGISTRY[resolveIconKey(providerId)];
  if (Icon) return <Icon className={cls} />;
  return (
    <span className={`inline-flex items-center justify-center rounded-sm border border-border/50 bg-sidebar/70 text-xs font-bold uppercase text-subtle-foreground ${cls}`}>
      {Array.from(providerId)[0] ?? "?"}
    </span>
  );
}
