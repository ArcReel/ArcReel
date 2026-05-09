import GeminiColor from "@lobehub/icons/es/Gemini/components/Color";
import GrokMono from "@lobehub/icons/es/Grok/components/Mono";
import OpenAIMono from "@lobehub/icons/es/OpenAI/components/Mono";
import VertexAIColor from "@lobehub/icons/es/VertexAI/components/Color";
import ViduColor from "@lobehub/icons/es/Vidu/components/Color";
import VolcengineColor from "@lobehub/icons/es/Volcengine/components/Color";

export const PROVIDER_NAMES: Record<string, string> = {
  "gemini-aistudio": "AI Studio",
  "gemini-vertex": "Vertex AI",
  ark: "火山方舟",
  grok: "Grok",
  openai: "OpenAI",
  vidu: "Vidu",
};

/**
 * 根据 providerId 渲染对应的供应商图标。
 * 支持 gemini-aistudio、gemini-vertex、grok、ark、openai、vidu，其余显示首字母。
 */
export function ProviderIcon({ providerId, className }: { providerId: string; className?: string }) {
  const cls = className ?? "h-6 w-6";
  if (providerId === "gemini-vertex") return <VertexAIColor className={cls} />;
  if (providerId.startsWith("gemini")) return <GeminiColor className={cls} />;
  if (providerId.startsWith("grok")) return <GrokMono className={cls} />;
  if (providerId === "ark") return <VolcengineColor className={cls} />;
  if (providerId === "openai") return <OpenAIMono className={cls} />;
  if (providerId === "vidu") return <ViduColor className={cls} />;
  // Fallback: first letter badge
  return (
    <span className={`inline-flex items-center justify-center rounded bg-gray-700 text-xs font-bold uppercase text-gray-300 ${cls}`}>
      {providerId[0]}
    </span>
  );
}
