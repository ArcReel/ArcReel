import GeminiColor from "@lobehub/icons/es/Gemini/components/Color";
import GrokMono from "@lobehub/icons/es/Grok/components/Mono";
import VertexAIColor from "@lobehub/icons/es/VertexAI/components/Color";
import JimengColor from "@lobehub/icons/es/Jimeng/components/Color";

export const PROVIDER_NAMES: Record<string, string> = {
  "gemini-aistudio": "AI Studio",
  "gemini-vertex": "Vertex AI",
  seedance: "Seedance",
  grok: "Grok",
};

/**
 * 根据 providerId 渲染对应的供应商图标。
 * 支持 gemini-aistudio、gemini-vertex、grok、seedance，其余显示首字母。
 */
export function ProviderIcon({ providerId, className }: { providerId: string; className?: string }) {
  const cls = className ?? "h-6 w-6";
  if (providerId === "gemini-vertex") return <VertexAIColor className={cls} />;
  if (providerId.startsWith("gemini")) return <GeminiColor className={cls} />;
  if (providerId.startsWith("grok")) return <GrokMono className={cls} />;
  if (providerId === "seedance") return <JimengColor className={cls} />;
  // Fallback: first letter badge
  return (
    <span className={`inline-flex items-center justify-center rounded bg-gray-700 text-xs font-bold uppercase text-gray-300 ${cls}`}>
      {providerId[0]}
    </span>
  );
}
